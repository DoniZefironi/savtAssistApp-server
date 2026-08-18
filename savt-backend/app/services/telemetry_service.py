import hmac
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import AlreadyExistsError, NotFoundError, PermissionDeniedError
from app.repositories.cabinet import CabinetRepository
from app.repositories.telemetry import (
    CabinetRegisterOverrideRepository,
    CabinetRegisterStateRepository,
    CabinetTelemetryEventRepository,
    RegisterDefinitionRepository,
)
from app.schemas.pagination import PageOut, make_page
from app.schemas.telemetry import (
    CabinetRegisterOverrideOut,
    CabinetRegisterOverridePatchIn,
    RegisterDefinitionOut,
    RegisterDefinitionPatchIn,
    TelemetryCurrentRegisterOut,
    TelemetryCurrentStateOut,
    TelemetryEventOut,
    TelemetryRegisterOut,
    TelemetryTargetOut,
)
from app.services.audit_service import AuditLogger
from app.services.realtime_events import publish_telemetry_event


# Секрет сверяем через hmac.compare_digest (не "=="), та же логика, что и у
# Telegram-вебхука (см. messenger_service.verify_telegram_secret) — защита от
# timing-атак, которыми можно подбирать секрет по времени ответа посимвольно.
def verify_telemetry_secret(header_value: str | None) -> bool:
    return bool(settings.telemetry_webhook_secret) and hmac.compare_digest(
        header_value or "", settings.telemetry_webhook_secret
    )


# Значение регистра — 16-битное слово (бит 0..15), каждый бит потенциально
# своя авария. include_unnamed=False (по умолчанию, обычный просмотр/мониторинг
# аварий) — в ответ попадают только
# биты, которые СЕЙЧАС взведены (=1) И названы в карте: на одно MQTT-сообщение
# приходит ~20 регистров по 16 бит каждый, и без фильтра аварию в этом шуме не
# найти. include_unnamed=True — для настройки самой карты: показывает все 16
# бит каждого регистра как есть (взведён/нет, названы или нет), чтобы понять,
# что означает ещё не названный бит, его сначала надо увидеть меняющимся.
# & 0xFFFF на входе — контроллер может прислать регистр как знаковое 16-битное
# число (-32768..32767), а не беззнаковое; без маски бит 15 отрицательных
# значений разъезжался бы на всю ширину Python-int при сдвиге вправо
def _register_bits(raw_value: int) -> list[int]:
    unsigned = raw_value & 0xFFFF
    return [(unsigned >> bit) & 1 for bit in range(16)]


# Все НАЗВАННЫЕ биты, которые реально переключились между old_value и
# new_value — (bit, name, старое значение бита, новое значение бита). Пустой
# список, если числа совпадают (контроллер обычно шлёт текущее состояние
# периодически, не только по изменению) или ни один изменившийся бит не
# назван в карте. Используется и для realtime-сигнала (любое переключение —
# 0→1 новая авария, 1→0 снятая), и для push-уведомлений (только 0→1, см.
# ingest ниже) — общая логика, чтобы не разъезжались друг с другом
def _named_bit_transitions(
    old_value: int, new_value: int, address: int, name_map: dict[tuple[int, int], str],
) -> list[tuple[int, str, int, int]]:
    if old_value == new_value:
        return []
    old_bits = _register_bits(old_value)
    new_bits = _register_bits(new_value)
    result = []
    for bit in range(16):
        if old_bits[bit] == new_bits[bit]:
            continue
        name = name_map.get((address, bit))
        if name is None:
            continue
        result.append((bit, name, old_bits[bit], new_bits[bit]))
    return result


def _decode_registers(
    raw_payload: dict, name_map: dict[tuple[int, int], str], include_unnamed: bool = False,
) -> list[TelemetryRegisterOut]:
    result = []
    for addr, raw_value in raw_payload.items():
        # Ключи из JSONB всегда приходят строками (JSON не умеет в нечисловые
        # ключи объекта) — переводим обратно в адрес-число здесь
        address = int(addr)
        for bit, bit_value in enumerate(_register_bits(raw_value)):
            name = name_map.get((address, bit))
            if not include_unnamed and (name is None or bit_value == 0):
                continue
            result.append(TelemetryRegisterOut(address=address, bit=bit, name=name, value=bit_value))
    return result


# Стандартная карта + переопределения конкретного ШУ поверх неё (override важнее).
# Общая для расшифровки на чтение и для решения, стоит ли слать realtime-сигнал
# на приём (TelemetryIngestService.ingest) — вынесена сюда, чтобы не заводить
# одну и ту же логику дважды.
async def _build_name_map(
    def_repo: RegisterDefinitionRepository, override_repo: CabinetRegisterOverrideRepository, cabinet_id: int,
) -> dict[tuple[int, int], str]:
    name_map = {(d.address, d.bit): d.name for d in await def_repo.list_all()}
    name_map.update({(o.address, o.bit): o.name for o in await override_repo.list_for_cabinet(cabinet_id)})
    return name_map


# Доступ к персональному WS-каналу телеметрии (/user-events/cabinets/{id}/telemetry) —
# та же схема, что у check_chat_access в chat_service.py: своя сессия, WS-хендлер
# не участвует в обычном Depends(get_session)
async def check_cabinet_telemetry_access(cabinet_id: int, user_id: int) -> bool:
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        return await CabinetRepository(session).user_has_access(user_id, cabinet_id)


# Автоочистка старой истории (см. schedule в main.py) — CabinetRegisterState
# ("текущее состояние") не трогает, у него нет понятия возраста вообще
async def prune_old_telemetry_history(session: AsyncSession, retention_days: int | None = None) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=retention_days if retention_days is not None else settings.telemetry_history_retention_days
    )
    deleted = await CabinetTelemetryEventRepository(session).delete_older_than(cutoff)
    await session.commit()
    return deleted


class TelemetryIngestService:
    """Приём вебхука от C#-прокси. Без пользовательского контекста — прокси
    про cabinet_id ничего не знает, только топик контроллера как есть."""
    def __init__(self, session: AsyncSession):
        self.session = session
        self.cabinet_repo = CabinetRepository(session)
        self.event_repo = CabinetTelemetryEventRepository(session)
        self.state_repo = CabinetRegisterStateRepository(session)
        self.def_repo = RegisterDefinitionRepository(session)
        self.override_repo = CabinetRegisterOverrideRepository(session)

    # Список "куда подключаться" — свой брокер у каждого ШУ (не общий на всех),
    # прокси периодически перечитывает это вместо статичного конфига
    async def list_targets(self) -> list[TelemetryTargetOut]:
        cabinets = await self.cabinet_repo.list_telemetry_targets()
        return [
            TelemetryTargetOut(
                cabinet_id=c.id,
                host=c.mqtt_host,
                port=c.mqtt_port,
                topic=c.mqtt_topic,
                username=c.mqtt_username,
                password=c.mqtt_password,
            )
            for c in cabinets
        ]

    async def ingest(self, topic: str, registers: dict[int, int], timestamp: datetime | None) -> None:
        cabinet = await self.cabinet_repo.get_by_mqtt_topic(topic)
        if cabinet is None:
            raise NotFoundError(f"ШУ с топиком '{topic}' не привязан")
        raw_payload = {str(address): value for address, value in registers.items()}
        # Сырое сообщение — в историю (для аудита, см. .../telemetry/history)...
        event = await self.event_repo.create(
            cabinet_id=cabinet.id,
            received_at=timestamp or datetime.now(timezone.utc),
            raw_payload=raw_payload,
        )
        # Старые значения — ДО перезаписи, нужны ниже, чтобы понять, что из
        # сообщения реально изменилось (а не просто повторный "тик" того же
        # состояния — контроллер шлёт его периодически, не только по факту
        # изменения)
        old_values = await self.state_repo.get_values_for_addresses(cabinet.id, list(registers.keys()))
        # ...и то же самое — в "текущее состояние" (перезаписывает предыдущее
        # значение каждого адреса), это и есть то, что реально отдаёт лента
        await self.state_repo.upsert_many(cabinet.id, registers)
        await self.session.commit()

        # Сигнал шлём, только если хотя бы один НАЗВАННЫЙ бит реально
        # переключился (0→1 — новая авария, 1→0 — авария снята) относительно
        # прошлого сообщения. Без этой проверки на каждое периодическое
        # повторение уже известного состояния (их в разы больше, чем реальных
        # изменений) улетал бы пуш ни о чём — а каждый такой пуш дёргает
        # админку/приложение на полный рефетч, что при частой телеметрии с
        # активной аварией быстро упирается в rate limit (см. инцидент
        # 2026-08-14 — ШУ 118 заваливал админку 429 именно так)
        name_map = await _build_name_map(self.def_repo, self.override_repo, cabinet.id)
        transitions_by_address = {
            address: _named_bit_transitions(old_values.get(address, 0), new_value, address, name_map)
            for address, new_value in registers.items()
        }
        if any(transitions_by_address.values()):
            await publish_telemetry_event(cabinet.id, event.id)

        # Push — только на НОВУЮ аварию (0→1), не на снятие: снятие видно и так
        # по тому, что строка пропала из ленты, отдельный пуш об этом был бы
        # избыточным. Один пуш на каждый новый взведённый бит, не один общий
        # на всё сообщение — в списке уведомлений это разные события
        new_alarms = [
            (address, bit, name)
            for address, transitions in transitions_by_address.items()
            for bit, name, old_bit, new_bit in transitions
            if old_bit == 0 and new_bit == 1
        ]
        if new_alarms:
            await self._notify_new_alarms(cabinet, new_alarms)

    async def _notify_new_alarms(self, cabinet, alarms: list[tuple[int, int, str]]) -> None:
        from app.services.notification_service import NotificationService

        members = await self.cabinet_repo.list_users_with_access(cabinet.id)
        if not members:
            return
        cabinet_name = cabinet.admin_internal_name or cabinet.object_number
        title = f"Авария ШУ «{cabinet_name}»"
        notif_service = NotificationService(self.session)
        for _address, _bit, alarm_name in alarms:
            for user, _membership in members:
                await notif_service.send(
                    user_id=user.id,
                    type_="cabinet_alarm",
                    title=title,
                    body=alarm_name,
                    data={"cabinet_id": cabinet.id},
                )


class UserTelemetryService:
    """Текущее состояние регистров ШУ для карточки в приложении (см.
    CabinetRegisterState) — та же проверка доступа, что и у документов/чата ШУ.
    Сырая история сообщений — отдельно, только для админки/аудита, см.
    list_history_for_cabinet_admin."""
    def __init__(self, session: AsyncSession):
        self.session = session
        self.cabinet_repo = CabinetRepository(session)
        self.event_repo = CabinetTelemetryEventRepository(session)
        self.state_repo = CabinetRegisterStateRepository(session)
        self.def_repo = RegisterDefinitionRepository(session)
        self.override_repo = CabinetRegisterOverrideRepository(session)

    async def get_current_state(
        self, user_id: int, cabinet_id: int, include_unnamed: bool = False,
    ) -> TelemetryCurrentStateOut:
        if not await self.cabinet_repo.user_has_access(user_id, cabinet_id):
            raise PermissionDeniedError("У вас нет доступа к этому ШУ")
        return await self._current_state(cabinet_id, include_unnamed)

    # Для админки/операторской панели — без проверки членства в проекте,
    # доступ уже ограничен ролью на уровне роутера (require_role)
    async def get_current_state_admin(
        self, cabinet_id: int, include_unnamed: bool = False,
    ) -> TelemetryCurrentStateOut:
        if await self.cabinet_repo.get_by_id(cabinet_id) is None:
            raise NotFoundError("ШУ не найден")
        return await self._current_state(cabinet_id, include_unnamed)

    async def _current_state(self, cabinet_id: int, include_unnamed: bool) -> TelemetryCurrentStateOut:
        states = await self.state_repo.list_for_cabinet(cabinet_id)
        name_map = await _build_name_map(self.def_repo, self.override_repo, cabinet_id)

        registers = []
        for state in states:
            for bit, bit_value in enumerate(_register_bits(state.value)):
                name = name_map.get((state.address, bit))
                if not include_unnamed and (name is None or bit_value == 0):
                    continue
                registers.append(TelemetryCurrentRegisterOut(
                    address=state.address, bit=bit, name=name, value=bit_value,
                    updated_at=state.updated_at,
                ))
        return TelemetryCurrentStateOut(registers=registers)

    # Сырая история сообщений — только для админки/операторов, разбор задним
    # числом ("когда началась авария"), не то, что видит обычный пользователь
    async def list_history_for_cabinet_admin(
        self, cabinet_id: int, page: int, size: int, include_unnamed: bool = False,
    ) -> PageOut[TelemetryEventOut]:
        if await self.cabinet_repo.get_by_id(cabinet_id) is None:
            raise NotFoundError("ШУ не найден")

        # Пагинация — по сырым событиям в БД, ДО фильтрации регистров, поэтому
        # на странице может оказаться меньше size событий, чем запрошено (см. README)
        events, total = await self.event_repo.list_for_cabinet(
            cabinet_id, offset=(page - 1) * size, limit=size,
        )
        name_map = await _build_name_map(self.def_repo, self.override_repo, cabinet_id)

        items = []
        for event in events:
            registers = _decode_registers(event.raw_payload, name_map, include_unnamed)
            if not registers and not include_unnamed:
                continue  # сообщение целиком без единого названного регистра — прячем, а не пустым пузырём
            items.append(TelemetryEventOut(
                id=event.id, received_at=event.received_at, registers=registers,
            ))
        return make_page(items, total, page, size)


class AdminRegisterMapService:
    """CRUD карты регистров — стандартной (для всех ШУ) и добавок на конкретный
    ШУ. Сама расшифровка (см. UserTelemetryService) читает эти данные, здесь —
    только их редактирование из админки."""
    def __init__(self, session: AsyncSession):
        self.session = session
        self.def_repo = RegisterDefinitionRepository(session)
        self.override_repo = CabinetRegisterOverrideRepository(session)
        self.cabinet_repo = CabinetRepository(session)
        self.audit = AuditLogger(session)

    async def list_definitions(self) -> list[RegisterDefinitionOut]:
        return [RegisterDefinitionOut.model_validate(d) for d in await self.def_repo.list_all()]

    async def create_definition(
        self, address: int, bit: int, name: str, description: str | None, actor_id: int, actor_role: str,
    ) -> RegisterDefinitionOut:
        existing = await self.def_repo.get_by_address_and_bit(address, bit)
        if existing is not None:
            raise AlreadyExistsError(f"Бит {bit} регистра {address} уже описан в стандартной карте")
        obj = await self.def_repo.create(address, bit, name, description)
        self.audit.log("register_definition.create", "register_definition", obj.id, actor_id, actor_role,
                       {"address": address, "bit": bit, "name": name})
        await self.session.commit()
        return RegisterDefinitionOut.model_validate(obj)

    async def update_definition(
        self, def_id: int, data: RegisterDefinitionPatchIn, actor_id: int, actor_role: str,
    ) -> RegisterDefinitionOut:
        obj = await self.def_repo.get_by_id(def_id)
        if obj is None:
            raise NotFoundError("Регистр не найден в стандартной карте")

        changes = data.model_dump(exclude_unset=True)
        # Адрес/бит меняются вместе — если поменяли только один из двух,
        # для проверки уникальности берём второй как есть у записи
        new_address = changes.get("address", obj.address)
        new_bit = changes.get("bit", obj.bit)
        if (new_address, new_bit) != (obj.address, obj.bit):
            existing = await self.def_repo.get_by_address_and_bit(new_address, new_bit)
            if existing is not None and existing.id != obj.id:
                raise AlreadyExistsError(f"Бит {new_bit} регистра {new_address} уже описан в стандартной карте")

        for field, value in changes.items():
            setattr(obj, field, value)
        self.audit.log("register_definition.update", "register_definition", obj.id, actor_id, actor_role, changes)
        await self.session.commit()
        # updated_at считается на сервере (onupdate=func.now()) — после UPDATE
        # объект в памяти его не знает, и без явного refresh Pydantic пытается
        # подгрузить атрибут синхронно при сериализации → MissingGreenlet.
        # При INSERT та же ситуация не всплывает — SQLAlchemy подтягивает
        # server_default сразу через RETURNING, для UPDATE так не происходит
        await self.session.refresh(obj)
        return RegisterDefinitionOut.model_validate(obj)

    async def delete_definition(self, def_id: int, actor_id: int, actor_role: str) -> None:
        obj = await self.def_repo.get_by_id(def_id)
        if obj is None:
            raise NotFoundError("Регистр не найден в стандартной карте")
        self.audit.log("register_definition.delete", "register_definition", def_id, actor_id, actor_role,
                       {"address": obj.address})
        await self.def_repo.delete(obj)
        await self.session.commit()

    async def list_overrides(self, cabinet_id: int) -> list[CabinetRegisterOverrideOut]:
        if await self.cabinet_repo.get_by_id(cabinet_id) is None:
            raise NotFoundError("ШУ не найден")
        return [
            CabinetRegisterOverrideOut.model_validate(o)
            for o in await self.override_repo.list_for_cabinet(cabinet_id)
        ]

    async def create_override(
        self, cabinet_id: int, address: int, bit: int, name: str, description: str | None,
        actor_id: int, actor_role: str,
    ) -> CabinetRegisterOverrideOut:
        if await self.cabinet_repo.get_by_id(cabinet_id) is None:
            raise NotFoundError("ШУ не найден")
        existing = await self.override_repo.get_by_cabinet_address_and_bit(cabinet_id, address, bit)
        if existing is not None:
            raise AlreadyExistsError(f"Бит {bit} регистра {address} уже переопределён для этого ШУ")
        obj = await self.override_repo.create(cabinet_id, address, bit, name, description)
        self.audit.log("cabinet_register_override.create", "cabinet", cabinet_id, actor_id, actor_role,
                       {"address": address, "bit": bit, "name": name})
        await self.session.commit()
        return CabinetRegisterOverrideOut.model_validate(obj)

    async def update_override(
        self, cabinet_id: int, override_id: int, data: CabinetRegisterOverridePatchIn,
        actor_id: int, actor_role: str,
    ) -> CabinetRegisterOverrideOut:
        obj = await self.override_repo.get_by_id(override_id)
        if obj is None or obj.cabinet_id != cabinet_id:
            raise NotFoundError("Переопределение не найдено")

        changes = data.model_dump(exclude_unset=True)
        new_address = changes.get("address", obj.address)
        new_bit = changes.get("bit", obj.bit)
        if (new_address, new_bit) != (obj.address, obj.bit):
            existing = await self.override_repo.get_by_cabinet_address_and_bit(cabinet_id, new_address, new_bit)
            if existing is not None and existing.id != obj.id:
                raise AlreadyExistsError(f"Бит {new_bit} регистра {new_address} уже переопределён для этого ШУ")

        for field, value in changes.items():
            setattr(obj, field, value)
        self.audit.log("cabinet_register_override.update", "cabinet", cabinet_id, actor_id, actor_role, changes)
        await self.session.commit()
        # см. update_definition выше — updated_at после UPDATE нужно перечитать явно
        await self.session.refresh(obj)
        return CabinetRegisterOverrideOut.model_validate(obj)

    async def delete_override(self, cabinet_id: int, override_id: int, actor_id: int, actor_role: str) -> None:
        obj = await self.override_repo.get_by_id(override_id)
        if obj is None or obj.cabinet_id != cabinet_id:
            raise NotFoundError("Переопределение не найдено")
        self.audit.log("cabinet_register_override.delete", "cabinet", cabinet_id, actor_id, actor_role,
                       {"address": obj.address})
        await self.override_repo.delete(obj)
        await self.session.commit()
