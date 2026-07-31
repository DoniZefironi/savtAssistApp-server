"""номер аккаунта берётся из Telegram; отдельный контактный телефон

Номер больше не вводится в форме регистрации: он приходит из контакта Telegram и
поэтому подтверждён по построению — сверять нечего, случай "номера не совпали"
исчезает. Данные формы паркуются в pending_registrations до момента, когда номер
станет известен (создать User без телефона не даёт constraint ck_users_phone_or_login).

users.contact_phone — необязательный рабочий номер: его видят операторы,
пользователь меняет свободно, на вход и безопасность он не влияет.

messenger_link_requests удаляется: рукопожатие с ботом заменено на
pending_registrations, а в сбросе пароля новые связки больше не заводятся.

Revision ID: 987f377c75cf
Revises: d3c17e880cd0
Create Date: 2026-07-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "987f377c75cf"
down_revision: Union[str, None] = "d3c17e880cd0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("contact_phone", sa.String(20), nullable=True))

    op.create_table(
        "pending_registrations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=True),
        sa.Column("user_type", sa.String(20), nullable=True),
        sa.Column("organization_name", sa.String(255), nullable=True),
        sa.Column("contact_phone", sa.String(20), nullable=True),
        # Чат, приславший /start — по нему находим заявку, когда придёт контакт
        sa.Column("external_chat_id", sa.String(64), nullable=True),
        # Заполняется после подтверждения номера контактом: с этого момента
        # пользователь уже создан и ждёт только ввода кода
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pending_registrations_token"), "pending_registrations", ["token"], unique=True)
    op.create_index(
        op.f("ix_pending_registrations_external_chat_id"), "pending_registrations", ["external_chat_id"]
    )
    op.create_index(op.f("ix_pending_registrations_expires_at"), "pending_registrations", ["expires_at"])

    # Рукопожатия больше не используются: регистрация идёт через
    # pending_registrations, а сброс пароля новых связок не заводит.
    # Данные эфемерные (TTL 15 минут), терять нечего.
    op.drop_table("messenger_link_requests")


def downgrade() -> None:
    op.create_table(
        "messenger_link_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("purpose", sa.String(30), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("external_chat_id", sa.String(64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_messenger_link_requests_token"), "messenger_link_requests", ["token"], unique=True)
    op.create_index(op.f("ix_messenger_link_requests_user_id"), "messenger_link_requests", ["user_id"])
    op.create_index(op.f("ix_messenger_link_requests_phone"), "messenger_link_requests", ["phone"])
    op.create_index(op.f("ix_messenger_link_requests_expires_at"), "messenger_link_requests", ["expires_at"])
    op.create_index(
        op.f("ix_messenger_link_requests_external_chat_id"), "messenger_link_requests", ["external_chat_id"]
    )

    op.drop_index(op.f("ix_pending_registrations_expires_at"), table_name="pending_registrations")
    op.drop_index(op.f("ix_pending_registrations_external_chat_id"), table_name="pending_registrations")
    op.drop_index(op.f("ix_pending_registrations_token"), table_name="pending_registrations")
    op.drop_table("pending_registrations")

    op.drop_column("users", "contact_phone")
