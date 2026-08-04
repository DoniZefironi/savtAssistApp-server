from datetime import datetime, timezone


def project_year(project) -> int:
    """Год проекта: из производственного номера ("26_170" → 2026), иначе по дате
    создания записи.

    Одно правило на три места: годовая папка на NAS
    (project_folder_service._year_folder_name), фильтр и сортировка по году
    (repositories.project.project_year_expr) и поле year в списке проектов.
    Разъедься они — фильтр перестал бы совпадать с тем, что видно в карточке и
    лежит на диске."""
    number = project.production_number or ""
    if len(number) >= 2 and number[:2].isdigit():
        return 2000 + int(number[:2])
    created = project.created_at or datetime.now(timezone.utc)
    return created.year
