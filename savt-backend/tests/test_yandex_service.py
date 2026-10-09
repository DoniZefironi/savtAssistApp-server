"""Клиент Yandex Cloud: эмбеддинги (с кэшем запросов), генерация, распознавание речи
(короткое и длинное через Object Storage), описание картинок и OCR. Сеть подменена
транспортом httpx: тест видит и отправленный запрос, и разбор ответа."""
import base64
import json
from types import SimpleNamespace

import httpx
import pytest

from app.config import settings
from app.services import storage_service, yandex_service


@pytest.fixture
def cloud(monkeypatch):
    """Подмена сети: handler(request) → httpx.Response; все запросы записываются."""
    state = SimpleNamespace(requests=[], handler=None)

    def transport_handler(request: httpx.Request) -> httpx.Response:
        state.requests.append(request)
        return state.handler(request)

    monkeypatch.setattr(yandex_service, "_client", httpx.AsyncClient(transport=httpx.MockTransport(transport_handler)))
    monkeypatch.setattr(yandex_service, "_QUERY_CACHE", {})
    monkeypatch.setattr(settings, "yandex_folder_id", "folder-1")
    monkeypatch.setattr(settings, "yandex_api_key", "key-1")
    monkeypatch.setattr(settings, "yandex_gpt_model", "yandexgpt")
    monkeypatch.setattr(settings, "yandex_vision_model", "gemma")
    monkeypatch.setattr(settings, "yandex_storage_bucket", "bucket")
    monkeypatch.setattr(settings, "yandex_storage_access_key_id", "storage-key")
    state.reply = lambda body, status=200: setattr(state, "handler", lambda request: httpx.Response(status, json=body))
    return state


def _json(request):
    return json.loads(request.content)


# --- эмбеддинги ---

async def test_document_and_query_embeddings_use_their_own_models(cloud):
    cloud.reply({"embedding": [0.1, 0.2]})

    doc = await yandex_service.embed_document("Текст документа")
    query = await yandex_service.embed_query("Вопрос")

    assert doc == query == [0.1, 0.2]
    first, second = cloud.requests
    assert _json(first) == {"modelUri": "emb://folder-1/text-search-doc/latest", "text": "Текст документа"}
    assert _json(second)["modelUri"] == "emb://folder-1/text-search-query/latest"
    assert first.headers["authorization"] == "Api-Key key-1" and first.url.path.endswith("/textEmbedding")


async def test_query_embeddings_are_cached_and_the_cache_is_bounded(cloud, monkeypatch):
    cloud.reply({"embedding": [1.0]})
    monkeypatch.setattr(yandex_service, "_CACHE_MAX", 2)

    await yandex_service.embed_query("Первый")
    await yandex_service.embed_query("  ПЕРВЫЙ ")        # регистр и пробелы не важны — из кэша
    assert len(cloud.requests) == 1

    await yandex_service.embed_query("Второй")
    await yandex_service.embed_query("Третий")           # вытесняет самый старый
    await yandex_service.embed_query("Первый")
    assert len(cloud.requests) == 4 and len(yandex_service._QUERY_CACHE) == 2


async def test_embedding_errors_are_raised_with_the_status(cloud):
    cloud.reply({"error": "quota"}, status=429)

    with pytest.raises(RuntimeError, match="Yandex embed 429"):
        await yandex_service.embed_document("текст")


# --- генерация ---

async def test_completion_sends_the_dialog_and_returns_the_answer(cloud):
    cloud.reply({"result": {"alternatives": [{"message": {"text": "Ответ модели"}}]}})

    answer = await yandex_service.complete("Системный промпт", [
        {"role": "user", "text": "Привет", "extra": "лишнее"},
        {"role": "assistant", "text": "Здравствуйте"},
    ])

    body = _json(cloud.requests[0])
    assert answer == "Ответ модели"
    assert body["modelUri"] == "gpt://folder-1/yandexgpt/latest"
    assert body["messages"] == [
        {"role": "system", "text": "Системный промпт"},
        {"role": "user", "text": "Привет"},
        {"role": "assistant", "text": "Здравствуйте"},
    ]
    assert body["completionOptions"]["stream"] is False


async def test_completion_errors_are_raised(cloud):
    cloud.reply({"error": "down"}, status=500)

    with pytest.raises(RuntimeError, match="Yandex complete 500"):
        await yandex_service.complete("s", [])


# --- короткое распознавание речи ---

async def test_short_recognition(cloud):
    cloud.reply({"result": "не работает насос"})

    text = await yandex_service.transcribe_voice(b"audio", format="lpcm", sample_rate_hertz=16000)
    default = await yandex_service.transcribe_voice(b"audio")

    request = cloud.requests[0]
    assert text == "не работает насос" and request.content == b"audio"
    assert dict(request.url.params) == {"folderId": "folder-1", "lang": "ru-RU", "format": "lpcm", "sampleRateHertz": "16000"}
    assert "sampleRateHertz" not in cloud.requests[1].url.params and default == "не работает насос"


async def test_recognition_requires_configuration_and_reports_failures(cloud, monkeypatch):
    cloud.reply({"error": "bad"}, status=400)
    with pytest.raises(RuntimeError, match="Yandex STT 400"):
        await yandex_service.transcribe_voice(b"audio")

    cloud.reply({})
    assert await yandex_service.transcribe_voice(b"audio") == ""      # ответ без текста — пустая строка

    monkeypatch.setattr(settings, "yandex_api_key", "")
    with pytest.raises(RuntimeError, match="не настроен"):
        await yandex_service.transcribe_voice(b"audio")


# --- длинное распознавание ---

@pytest.fixture
def storage(monkeypatch):
    calls = SimpleNamespace(uploaded=[], deleted=[])
    monkeypatch.setattr(storage_service, "upload", lambda key, data: calls.uploaded.append((key, data)))
    monkeypatch.setattr(storage_service, "presigned_url", lambda key: f"https://storage.test/{key}?sig=1")
    monkeypatch.setattr(storage_service, "delete", lambda key: calls.deleted.append(key))
    monkeypatch.setattr(yandex_service, "_LRR_POLL_INTERVAL", 0.001)
    return calls


async def test_long_recognition_polls_until_done_and_cleans_storage(cloud, storage):
    polls = []

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"id": "op-1"})
        polls.append(1)
        if len(polls) < 3:
            return httpx.Response(200, json={"done": False})
        return httpx.Response(200, json={"done": True, "response": {"chunks": [
            {"alternatives": [{"text": "первая часть"}]}, {"alternatives": []}, {"alternatives": [{"text": "вторая"}]},
        ]}})

    cloud.handler = handler

    text = await yandex_service.transcribe_voice_long(b"big audio", format="lpcm", sample_rate_hertz=48000)

    assert text == "первая часть вторая" and len(polls) == 3
    start = _json(cloud.requests[0])
    assert start["config"]["specification"] == {"audioEncoding": "LINEAR16_PCM", "languageCode": "ru-RU", "sampleRateHertz": 48000}
    assert start["audio"]["uri"].startswith("https://storage.test/stt-tmp/")
    [(key, data)] = storage.uploaded
    assert data == b"big audio" and storage.deleted == [key]          # временный файл удалён


@pytest.mark.parametrize("failure", ["start", "poll", "operation-error", "timeout"])
async def test_long_recognition_failures_still_clean_up(cloud, storage, monkeypatch, failure):
    monkeypatch.setattr(yandex_service, "_LRR_TIMEOUT", 0.02)

    def handler(request):
        if request.method == "POST":
            return httpx.Response(500 if failure == "start" else 200, json={"id": "op-1"})
        if failure == "poll":
            return httpx.Response(503, text="busy")
        if failure == "operation-error":
            return httpx.Response(200, json={"done": True, "error": {"message": "плохой звук"}})
        return httpx.Response(200, json={"done": False})

    cloud.handler = handler

    with pytest.raises(RuntimeError):
        await yandex_service.transcribe_voice_long(b"audio")

    assert len(storage.deleted) == 1


async def test_long_recognition_needs_object_storage(cloud, monkeypatch):
    monkeypatch.setattr(settings, "yandex_storage_bucket", "")

    with pytest.raises(RuntimeError, match="Object Storage"):
        await yandex_service.transcribe_voice_long(b"audio")


# --- картинки ---

async def test_image_description_goes_through_the_vision_model(cloud):
    cloud.reply({"choices": [{"message": {"content": "Схема подключения"}}]})

    text = await yandex_service.analyze_image(b"png-bytes", "Опиши", mime_type="image/png")

    request = cloud.requests[0]
    body = _json(request)
    assert text == "Схема подключения" and request.headers["openai-project"] == "folder-1"
    assert body["model"] == "gpt://folder-1/gemma"
    parts = body["messages"][0]["content"]
    assert parts[0] == {"type": "text", "text": "Опиши"}
    assert parts[1]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(b"png-bytes").decode()


async def test_image_description_failures(cloud, monkeypatch):
    cloud.reply({"error": "no"}, status=403)
    with pytest.raises(RuntimeError, match="Yandex vision 403"):
        await yandex_service.analyze_image(b"x", "p")

    cloud.reply({"unexpected": True})
    with pytest.raises(RuntimeError, match="неожиданный формат"):
        await yandex_service.analyze_image(b"x", "p")

    monkeypatch.setattr(settings, "yandex_folder_id", "")
    with pytest.raises(RuntimeError, match="не настроен"):
        await yandex_service.analyze_image(b"x", "p")


async def test_ocr_collects_lines_from_every_block(cloud):
    cloud.reply({"results": [{"results": [{"textDetection": {"pages": [{"blocks": [
        {"lines": [{"text": "Первая строка"}, {"text": ""}, {"text": "Вторая"}]},
        {"lines": [{"text": "Третья"}]},
    ]}]}}]}, {"results": []}]})

    text = await yandex_service.ocr_image(b"scan")

    body = _json(cloud.requests[0])
    assert text == "Первая строка\nВторая\nТретья"
    assert body["folderId"] == "folder-1" and body["analyze_specs"][0]["content"] == base64.b64encode(b"scan").decode()


async def test_ocr_errors_are_raised(cloud):
    cloud.reply({"error": "x"}, status=500)

    with pytest.raises(RuntimeError, match="Yandex Vision OCR 500"):
        await yandex_service.ocr_image(b"scan")


async def test_a_shared_client_is_created_lazily(monkeypatch):
    monkeypatch.setattr(yandex_service, "_client", None)

    first = yandex_service._get_client()

    assert first is yandex_service._get_client() and isinstance(first, httpx.AsyncClient)
    await first.aclose()
