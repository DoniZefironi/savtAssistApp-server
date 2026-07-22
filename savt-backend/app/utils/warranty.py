from datetime import datetime, timedelta, timezone


def warranty_status(ends_at: datetime | None) -> str:
    if ends_at is None:
        return "none"
    now = datetime.now(timezone.utc)
    if ends_at < now:
        return "expired"
    if ends_at < now + timedelta(days=30):
        return "expiring_soon"
    return "active"
