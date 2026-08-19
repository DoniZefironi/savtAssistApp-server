"""GIN-индексы под нечёткий поиск (pg_trgm)

fuzzy_condition/match_score (app/utils/db.py) используются в 8 репозиториях,
но под них не было ни одного индекса — каждый поисковый запрос делал
последовательное сканирование таблицы целиком. Индексы — по тому же
выражению, что строит сам поиск: normalize_search_text(колонка), либо
normalize_search_text(колонка::VARCHAR) для JSON-полей (phones/emails
ProjectContact) — синтаксис CAST должен совпадать буквально, иначе planner
индекс не заметит (проверено компиляцией реального запроса).

Таблицы небольшие (сотни-тысячи строк) — CREATE INDEX без CONCURRENTLY
(в транзакции миграции, как и везде в проекте); если когда-нибудь разрастутся
настолько, что блокировка на время построения станет заметна, тогда и стоит
пересматривать на CONCURRENTLY вне транзакции.

Revision ID: d3f8b2c6a915
Revises: e6a3c8f1d4b7
Create Date: 2026-08-17 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = "d3f8b2c6a915"
down_revision: Union[str, None] = "e6a3c8f1d4b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (имя индекса, таблица, SQL-выражение внутри normalize_search_text)
_INDEXES = [
    # projects
    ("ix_trgm_projects_name", "projects", "name"),
    ("ix_trgm_projects_production_number", "projects", "production_number"),
    ("ix_trgm_projects_company_name", "projects", "company_name"),
    # project_contacts
    ("ix_trgm_project_contacts_full_name", "project_contacts", "full_name"),
    ("ix_trgm_project_contacts_post", "project_contacts", "post"),
    ("ix_trgm_project_contacts_phones", "project_contacts", "CAST(phones AS VARCHAR)"),
    ("ix_trgm_project_contacts_emails", "project_contacts", "CAST(emails AS VARCHAR)"),
    # project_share_requests
    ("ix_trgm_project_share_requests_user_comment", "project_share_requests", "user_comment"),
    ("ix_trgm_project_share_requests_admin_response", "project_share_requests", "admin_response"),
    # cabinets
    ("ix_trgm_cabinets_type", "cabinets", "type"),
    ("ix_trgm_cabinets_object_number", "cabinets", "object_number"),
    ("ix_trgm_cabinets_admin_internal_name", "cabinets", "admin_internal_name"),
    ("ix_trgm_cabinets_purpose", "cabinets", "purpose"),
    ("ix_trgm_cabinets_description", "cabinets", "description"),
    ("ix_trgm_cabinets_admin_comment", "cabinets", "admin_comment"),
    # cabinet_addition_requests
    ("ix_trgm_cabinet_addition_requests_user_comment", "cabinet_addition_requests", "user_comment"),
    ("ix_trgm_cabinet_addition_requests_admin_response", "cabinet_addition_requests", "admin_response"),
    # users
    ("ix_trgm_users_full_name", "users", "full_name"),
    ("ix_trgm_users_phone", "users", "phone"),
    ("ix_trgm_users_organization_name", "users", "organization_name"),
    ("ix_trgm_users_login", "users", "login"),
    ("ix_trgm_users_email", "users", "email"),
    # document_requests
    ("ix_trgm_document_requests_doc_type", "document_requests", "doc_type"),
    ("ix_trgm_document_requests_user_message", "document_requests", "user_message"),
    ("ix_trgm_document_requests_admin_response", "document_requests", "admin_response"),
    # service_requests
    ("ix_trgm_service_requests_request_type", "service_requests", "request_type"),
    ("ix_trgm_service_requests_description", "service_requests", "description"),
    # faq_categories / faq_entries
    ("ix_trgm_faq_categories_name", "faq_categories", "name"),
    ("ix_trgm_faq_entries_question", "faq_entries", "question"),
    ("ix_trgm_faq_entries_answer", "faq_entries", "answer"),
    # kb_categories / kb_articles
    ("ix_trgm_kb_categories_name", "kb_categories", "name"),
    ("ix_trgm_kb_categories_description", "kb_categories", "description"),
    ("ix_trgm_kb_articles_title", "kb_articles", "title"),
    ("ix_trgm_kb_articles_content", "kb_articles", "content"),
]


def upgrade() -> None:
    for name, table, expr in _INDEXES:
        op.execute(
            f"CREATE INDEX {name} ON {table} "
            f"USING gin (normalize_search_text({expr}) gin_trgm_ops)"
        )


def downgrade() -> None:
    for name, _table, _expr in _INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
