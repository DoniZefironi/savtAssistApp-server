"""Предел длины списков во входных схемах: каждое вложение — отдельная запись в БД, а без
предела один запрос мог бы создать их тысячи."""
import pytest
from pydantic import ValidationError

from app.core.security import create_access_token
from app.schemas.chat import MessageCreateIn
from app.schemas.notifications import PromoScheduleUpdateIn
from app.schemas.reclamation import ReclamationCreateIn
from app.schemas.tags import DocumentTagsIn

LOCATION = {"latitude": 53.9, "longitude": 27.5}
RECLAMATION = dict(
    object_type="line", object_details={"serial_number": "1"}, description="Течёт насос",
    contact_name="Иван", contact_phone="+375291112233", contact_email="ivan@example.by",
)
RECLAMATION_FILE = {"file_url": "/static/files/photo.jpg"}


@pytest.mark.parametrize("build", [
    lambda n: MessageCreateIn(attachments=[LOCATION] * n),
    lambda n: ReclamationCreateIn(**RECLAMATION, attachments=[RECLAMATION_FILE] * n),
])
def test_attachments_are_limited_to_twenty(build):
    assert len(build(20).attachments) == 20
    with pytest.raises(ValidationError):
        build(21)


@pytest.mark.parametrize("build", [
    lambda n: DocumentTagsIn(tag_ids=list(range(1, n + 1))),
    lambda n: PromoScheduleUpdateIn(message_ids=list(range(1, n + 1))),
])
def test_admin_id_lists_are_limited_to_a_hundred(build):
    assert build(100)
    with pytest.raises(ValidationError):
        build(101)


async def test_a_message_with_too_many_attachments_is_refused_over_http(api, make_user, make_chat):
    user = await make_user()
    chat = await make_chat(user, "notes")
    headers = {"Authorization": f"Bearer {create_access_token(user_id=user.id, role='user')}"}

    allowed = await api.post(f"/chats/{chat.id}/messages", headers=headers, json={"attachments": [LOCATION] * 20})
    refused = await api.post(f"/chats/{chat.id}/messages", headers=headers, json={"attachments": [LOCATION] * 21})

    assert allowed.status_code == 201 and len(allowed.json()["attachments"]) == 20
    assert refused.status_code == 422
