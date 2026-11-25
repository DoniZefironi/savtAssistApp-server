from sqlalchemy import Integer, String, and_, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cabinets import Cabinet
from app.models.project import Project
from app.schemas.telephony import ObjectMatchOut, ResolveObjectIn, ResolveObjectOut


async def resolve_object(session: AsyncSession, data: ResolveObjectIn) -> ResolveObjectOut:
    project_fields = (
        Project.id.label("project_id"),
        Project.production_number.label("project_number"),
        Project.bitrix_deal_id,
    )
    queries = []
    if data.kind != "cabinet":
        queries.append(select(
            literal("project").label("kind"),
            literal(None, Integer).label("cabinet_id"),
            literal(None, String).label("cabinet_number"),
            *project_fields,
        ).where(
            Project.production_number == data.number,
            Project.deleted_at.is_(None),
        ))
    if data.kind != "project":
        queries.append(select(
            literal("cabinet").label("kind"),
            Cabinet.id.label("cabinet_id"),
            Cabinet.object_number.label("cabinet_number"),
            *project_fields,
        ).select_from(Cabinet).outerjoin(Project, and_(
            Cabinet.project_id == Project.id,
            Project.deleted_at.is_(None),
        )).where(
            Cabinet.object_number == data.number,
            Cabinet.deleted_at.is_(None),
        ))

    # Обе таблицы проверяются одним запросом, двух строк достаточно для неоднозначности.
    query = queries[0] if len(queries) == 1 else union_all(*queries)
    result = await session.execute(query.limit(2))
    matches = result.mappings().all()
    if not matches:
        return ResolveObjectOut(status="not_found")
    if len(matches) > 1:
        return ResolveObjectOut(status="ambiguous")
    return ResolveObjectOut(status="found", match=ObjectMatchOut.model_validate(matches[0]))
