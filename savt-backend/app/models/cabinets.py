from datetime import datetime
from sqlalchemy import Double, Integer, String, DateTime, ForeignKey, func, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Cabinet(Base):
    __tablename__ = "cabinets"

    id: Mapped[int] = mapped_column(primary_key=True)
    # админская группировка по проекту (не влияет на владельцев ШУ)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # модель ШУ
    type: Mapped[str] = mapped_column(String(100), index=True)
    # номер объекта
    object_number: Mapped[str] = mapped_column(String(100))
    # описание
    description: Mapped[str | None] = mapped_column(Text)
    # начало гарантии (null - гарантии нет вообще)
    warranty_starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # окончание гарантии (null - гарантии нет вообще)
    warranty_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    # рабочее название
    admin_internal_name: Mapped[str | None] = mapped_column(String(200))
    # комментарий администратора
    admin_comment: Mapped[str | None] = mapped_column(Text)
    # назначение
    purpose: Mapped[str | None] = mapped_column(String(200))
    # геолокация ШУ
    latitude: Mapped[float | None] = mapped_column(Double)
    longitude: Mapped[float | None] = mapped_column(Double)
    # MQTT-топик контроллера этого ШУ (например "26_001/1/data") — прокси на C#
    # не знает cabinet_id, шлёт вебхук с этим топиком как есть, по нему и ищем ШУ
    mqtt_topic: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True, index=True)
    # Брокер этого конкретного ШУ — свой у каждого контроллера, не общий на всех
    # (см. GET /webhooks/telemetry/targets, которым прокси узнаёт, куда подключаться)
    mqtt_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mqtt_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mqtt_username: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Хранится как есть (не хешируется) — нужен серверу целиком, чтобы отдать
    # прокси для подключения к брокеру, это не пароль пользователя приложения
    mqtt_password: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # id записи во внешнем приложении управления SIM-картами (10.1.0.67:5000) —
    # сами данные (статус/IP/телефон) там и остаются, здесь только связь.
    # unique — одна SIM физически стоит только в одном ШУ одновременно
    sim_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True, index=True)
    # дата создания
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    # дата обработки
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    # soft-delete: если не NULL - ШУ считается удалённым, скрыт из поиска/списков/гео
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    def __repr__(self) -> str:
        return f"<Cabinet id={self.id} object_number={self.object_number}>"