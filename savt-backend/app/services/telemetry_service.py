import hmac
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import AlreadyExistsError, NotFoundError, PermissionDeniedError
from app.repositories.cabinet import CabinetRepository
from app.repositories.telemetry import (
    CabinetRegisterOverrideRepository,
    CabinetTelemetryEventRepository,
    RegisterDefinitionRepository,
)
from app.schemas.pagination import PageOut, make_page
from app.schemas.telemetry import (
    CabinetRegisterOverrideOut,
    RegisterDefinitionOut,
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


# include_unnamed=False (по умолчанию, обычный просмотр/мониторинг аварий) —
# регистры без названия в карте не попадают в ответ вообще: на одно MQTT-сообщение
# приходит ~20 регистров, из которых обычно назван едва ли не один, и без
# фильтра аварию среди сплошного шума не найти. include_unnamed=True — для
# настройки самой карты: чтобы понять, что означает ещё не названный регистр,
# его сначала надо увидеть меняющимся.
def _decode_registers(
    raw_payload: dict, name_map: dict[int, str], include_unnamed: bool = False,
) -> list[TelemetryRegisterOut]:
    result = []
    for addr, value in raw_payload.items():
        # Ключи из JSONB всегда приходят строками (JSON не умеет в нечисловые
        # ключи объекта) — переводим обратно в адрес-число здесь
        address = int(addr)
        name = name_map.get(address)
        if name is None and not include_unnamed:
            continue
        result.append(TelemetryRegisterOut(address=address, name=name, value=value))
    return result


# Стандартная карта + переопределения конкретного ШУ поверх неё (override важнее).
# Общая для расшифровки на чтение (UserTelemetryService) и для решения, стоит ли
# слать realtime-сигнал на приём (TelemetryIngestService.ingest) — вынесена сюда,
# чтобы не заводить одну и ту же логику дважды.
async def _build_name_map(
    def_repo: RegisterDefinitionRepository, override_repo: CabinetRegisterOverrideRepository, cabinet_id: int,
) -> dict[int, str]:
    name_map = {d.address: d.name for d in await def_repo.list_all()}
    name_map.update({o.address: o.name for o in await override_repo.list_for_cabinet(cabinet_id)})
    return name_map


# Доступ к персональному WS-каналу телеметрии (/user-events/cabinets/{id}/telemetry) —
# та же схема, что у check_chat_access в chat_service.py: своя сессия, WS-хендлер
# не участвует в обычном Depends(get_session)
async def check_cabinet_telemetry_access(cabinet_id: int, user_id: int) -> bool:
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        return await CabinetRepository(session).user_has_access(user_id, cabinet_id)


class TelemetryIngestService:
    """Приём вебхука от C#-прокси. Без пользовательского контекста — прокси
    про cabinet_id ничего не знает, только топик контроллера как есть."""
    def __init__(self, session: AsyncSession):
        self.session = session
        self.cabinet_repo = CabinetRepository(session)
        self.event_repo = CabinetTelemetryEventRepository(session)
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
        event = await self.event_repo.create(
            cabinet_id=cabinet.id,
            received_at=timestamp or datetime.now(timezone.utc),
            raw_payload=raw_payload,
        )
        await self.session.commit()

        # Сигнал шлём, только если в сообщении есть хоть один названный в карте
        # регистр — иначе на каждое сырое сообщение (их в разы больше значимых,
        # см. include_unnamed в README) фронт получал бы пуш ни о чём: по умолчанию
        # GET .../telemetry такое сообщение всё равно не покажет (см. _list)
        name_map = await _build_name_map(self.def_repo, self.override_repo, cabinet.id)
        if any(int(addr) in name_map for addr in raw_payload):
            await publish_telemetry_event(cabinet.id, event.id)


class UserTelemetryService:
    """Лента событий ШУ для карточки в приложении — та же проверка доступа
    (членство в проекте ШУ), что и у документов/чата ШУ."""
    def __init__(self, session: AsyncSession):
        self.session = session
        self.cabinet_repo = CabinetRepository(session)
        self.event_repo = CabinetTelemetryEventRepository(session)
        self.def_repo = RegisterDefinitionRepository(session)
        self.override_repo = CabinetRegisterOverrideRepository(session)

    async def list_for_cabinet(
        self, user_id: int, cabinet_id: int, page: int, size: int, include_unnamed: bool = False,
    ) -> PageOut[TelemetryEventOut]:
        if not await self.cabinet_repo.user_has_access(user_id, cabinet_id):
            raise PermissionDeniedError("У вас нет доступа к этому ШУ")
        return await self._list(cabinet_id, page, size, include_unnamed)

    # Для админки/операторской панели — без проверки членства в проекте,
    # доступ уже ограничен ролью на уровне роутера (require_role)
    async def list_for_cabinet_admin(
        self, cabinet_id: int, page: int, size: int, include_unnamed: bool = False,
    ) -> PageOut[TelemetryEventOut]:
        if await self.cabinet_repo.get_by_id(cabinet_id) is None:
            raise NotFoundError("ШУ не найден")
        return await self._list(cabinet_id, page, size, include_unnamed)

    async def _list(
        self, cabinet_id: int, page: int, size: int, include_unnamed: bool = False,
    ) -> PageOut[TelemetryEventOut]:
        # Пагинация — по сырым событиям в БД, ДО фильтрации регистров. Значит
        # при include_unnamed=False (обычный режим) на странице может оказаться
        # меньше size событий, чем запрошено — часть после фильтрации/пропуска
        # пустых просто исчезает. Точную пагинацию "только по значимым событиям"
        # пришлось бы делать через JSONB-фильтр в самом SQL-запросе — усложнение,
        # не оправданное, пока карта регистров разреженная (по мере её заполнения
        # разрыв между "сырых событий" и "значимых событий" будет только сокращаться)
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
        self, address: int, name: str, description: str | None, actor_id: int, actor_role: str,
    ) -> RegisterDefinitionOut:
        existing = await self.def_repo.get_by_address(address)
        if existing is not None:
            raise AlreadyExistsError(f"Регистр {address} уже описан в стандартной карте")
        obj = await self.def_repo.create(address, name, description)
        self.audit.log("register_definition.create", "register_definition", obj.id, actor_id, actor_role,
                       {"address": address, "name": name})
        await self.session.commit()
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
        self, cabinet_id: int, address: int, name: str, description: str | None,
        actor_id: int, actor_role: str,
    ) -> CabinetRegisterOverrideOut:
        if await self.cabinet_repo.get_by_id(cabinet_id) is None:
            raise NotFoundError("ШУ не найден")
        existing = await self.override_repo.get_by_cabinet_and_address(cabinet_id, address)
        if existing is not None:
            raise AlreadyExistsError(f"Регистр {address} уже переопределён для этого ШУ")
        obj = await self.override_repo.create(cabinet_id, address, name, description)
        self.audit.log("cabinet_register_override.create", "cabinet", cabinet_id, actor_id, actor_role,
                       {"address": address, "name": name})
        await self.session.commit()
        return CabinetRegisterOverrideOut.model_validate(obj)

    async def delete_override(self, cabinet_id: int, override_id: int, actor_id: int, actor_role: str) -> None:
        obj = await self.override_repo.get_by_id(override_id)
        if obj is None or obj.cabinet_id != cabinet_id:
            raise NotFoundError("Переопределение не найдено")
        self.audit.log("cabinet_register_override.delete", "cabinet", cabinet_id, actor_id, actor_role,
                       {"address": obj.address})
        await self.override_repo.delete(obj)
        await self.session.commit()
