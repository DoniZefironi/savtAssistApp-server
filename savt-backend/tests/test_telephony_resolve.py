from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings


PATH = "/internal/telephony/resolve-object"
SECRET = "sip-test-secret"


@pytest.fixture
def sip_headers(monkeypatch):
    monkeypatch.setattr(settings, "telephony_api_secret", SECRET)
    return {"X-Telephony-Secret": SECRET}


async def test_exact_project_number_preserves_leading_zeroes(api, sip_headers, make_project):
    project = await make_project(production_number="025_001", bitrix_deal_id="4582")
    await make_project(production_number="25_001", bitrix_deal_id="4583")

    response = await api.post(PATH, headers=sip_headers, json={"number": "025_001"})

    assert response.status_code == 200
    assert response.json() == {"status": "found", "match": {
        "kind": "project", "cabinet_id": None, "cabinet_number": None,
        "project_id": project.id, "project_number": "025_001", "bitrix_deal_id": "4582",
    }}


async def test_cabinet_uses_the_linked_project_id(api, sip_headers, make_project, make_cabinet):
    await make_project(production_number="25_125", bitrix_deal_id="4582")
    actual = await make_project(production_number="25_099", bitrix_deal_id="4583")
    cabinet = await make_cabinet(object_number="25_125_2", project_id=actual.id)

    response = await api.post(PATH, headers=sip_headers, json={"number": "25_125_2"})

    assert response.status_code == 200
    assert response.json() == {"status": "found", "match": {
        "kind": "cabinet", "cabinet_id": cabinet.id, "cabinet_number": "25_125_2",
        "project_id": actual.id, "project_number": "25_099", "bitrix_deal_id": "4583",
    }}


async def test_nonstandard_number_without_linked_project(api, sip_headers, make_cabinet):
    number = "ШУ-007/А-02 rev.B"
    cabinet = await make_cabinet(object_number=number)

    response = await api.post(PATH, headers=sip_headers, json={"number": number, "kind": "cabinet"})

    assert response.status_code == 200
    assert response.json() == {"status": "found", "match": {
        "kind": "cabinet", "cabinet_id": cabinet.id, "cabinet_number": number,
        "project_id": None, "project_number": None, "bitrix_deal_id": None,
    }}


@pytest.mark.parametrize("kind", ["project", "cabinet"])
async def test_duplicates_in_one_catalog_stay_ambiguous(api, sip_headers, make_project, make_cabinet, kind):
    number = "25-007"
    if kind == "project":
        await make_project(production_number=number)
        await make_project(production_number=number)
    else:
        await make_cabinet(object_number=number)
        await make_cabinet(object_number=number)

    response = await api.post(PATH, headers=sip_headers, json={"number": number, "kind": kind})

    assert response.status_code == 200
    assert response.json() == {"status": "ambiguous", "match": None}


async def test_kind_selects_the_catalog_on_cross_catalog_collision(api, sip_headers, make_project, make_cabinet):
    number = "25_003"
    await make_project(production_number=number)
    await make_cabinet(object_number=number)

    response = await api.post(PATH, headers=sip_headers, json={"number": number})
    assert response.status_code == 200
    assert response.json() == {"status": "ambiguous", "match": None}
    for kind in ("project", "cabinet"):
        response = await api.post(PATH, headers=sip_headers, json={"number": number, "kind": kind})
        assert response.status_code == 200
        assert response.json()["match"]["kind"] == kind


async def test_linked_project_without_a_deal_returns_null(api, sip_headers, make_project, make_cabinet):
    project = await make_project(production_number="25_125")
    await make_cabinet(object_number="25_125_2", project_id=project.id)

    response = await api.post(PATH, headers=sip_headers, json={"number": "25_125_2"})

    assert response.status_code == 200
    assert response.json()["match"]["project_id"] == project.id
    assert response.json()["match"]["bitrix_deal_id"] is None


async def test_unknown_number_and_deleted_records_return_not_found(api, sip_headers, make_project, make_cabinet):
    deleted_at = datetime.now(timezone.utc)
    await make_project(production_number="25_001", deleted_at=deleted_at)
    await make_cabinet(object_number="25_001", deleted_at=deleted_at)

    for number in ("25_001", "25_00"):
        response = await api.post(PATH, headers=sip_headers, json={"number": number})
        assert response.status_code == 200
        assert response.json() == {"status": "not_found", "match": None}


async def test_deleted_project_is_not_returned_as_a_cabinet_link(api, sip_headers, make_project, make_cabinet):
    project = await make_project(
        production_number="25_125", bitrix_deal_id="4582", deleted_at=datetime.now(timezone.utc),
    )
    await make_cabinet(object_number="25_125_2", project_id=project.id)

    response = await api.post(PATH, headers=sip_headers, json={"number": "25_125_2"})

    assert response.status_code == 200
    assert response.json()["status"] == "found"
    match = response.json()["match"]
    assert match["project_id"] is None and match["project_number"] is None and match["bitrix_deal_id"] is None


@pytest.mark.parametrize("headers", [{}, {"X-Telephony-Secret": "wrong"}, {"X-Telephony-Secret": "é"}])
async def test_missing_or_invalid_secret_is_forbidden(api, sip_headers, headers):
    # Байты позволяют проверить произвольное значение заголовка, полученное по сети.
    raw_headers = [(key.encode(), value.encode("latin-1")) for key, value in headers.items()]
    response = await api.post(PATH, headers=raw_headers, json={"number": "25_125"})
    assert response.status_code == 403


async def test_empty_server_secret_is_forbidden(api, monkeypatch, sip_headers):
    monkeypatch.setattr(settings, "telephony_api_secret", "")
    response = await api.post(PATH, headers=sip_headers, json={"number": "25_125"})
    assert response.status_code == 403


@pytest.mark.parametrize("payload", [{"number": 25125}, {"number": "25_125", "kind": "object"}])
async def test_invalid_input_is_rejected(api, sip_headers, payload):
    response = await api.post(PATH, headers=sip_headers, json=payload)
    assert response.status_code == 422


async def test_lookup_failure_is_an_api_error(api, sip_headers, db_session, monkeypatch):
    monkeypatch.setattr(db_session, "execute", AsyncMock(side_effect=SQLAlchemyError("lookup failed")))
    response = await api.post(PATH, headers=sip_headers, json={"number": "25_125"})
    assert response.status_code == 500
