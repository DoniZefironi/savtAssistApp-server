import asyncio
import base64
import time
import uuid

import httpx

from app.config import settings
from app.services import storage_service

_BASE = "https://llm.api.cloud.yandex.net/foundationModels/v1"

_client: httpx.AsyncClient | None = None

# Кэш эмбеддингов запросов (одинаковые вопросы не перезапрашиваем)
_QUERY_CACHE: dict[str, list[float]] = {}
_CACHE_MAX = 500


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=30,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client


def _headers() -> dict:
    return {
        "Authorization": f"Api-Key {settings.yandex_api_key}",
        "Content-Type": "application/json",
    }


async def embed_document(text: str) -> list[float]:
    return await _embed(text, "text-search-doc")


async def embed_query(text: str) -> list[float]:
    key = text.strip().lower()[:200]
    if key in _QUERY_CACHE:
        return _QUERY_CACHE[key]
    vec = await _embed(text, "text-search-query")
    if len(_QUERY_CACHE) >= _CACHE_MAX:
        _QUERY_CACHE.pop(next(iter(_QUERY_CACHE)))
    _QUERY_CACHE[key] = vec
    return vec


async def _embed(text: str, model_type: str) -> list[float]:
    model_uri = f"emb://{settings.yandex_folder_id}/{model_type}/latest"
    resp = await _get_client().post(
        f"{_BASE}/textEmbedding",
        headers=_headers(),
        json={"modelUri": model_uri, "text": text},
    )
    if not resp.is_success:
        raise RuntimeError(f"Yandex embed {resp.status_code}: {resp.text}")
    return resp.json()["embedding"]


async def complete(system_prompt: str, messages: list[dict]) -> str:
    model_uri = f"gpt://{settings.yandex_folder_id}/{settings.yandex_gpt_model}/latest"

    yandex_messages = [{"role": "system", "text": system_prompt}]
    for m in messages:
        yandex_messages.append({"role": m["role"], "text": m["text"]})

    resp = await _get_client().post(
        f"{_BASE}/completion",
        headers=_headers(),
        json={
            "modelUri": model_uri,
            "completionOptions": {
                "stream": False,
                "temperature": 0.3,
                "maxTokens": 1000,
            },
            "messages": yandex_messages,
        },
    )
    if not resp.is_success:
        raise RuntimeError(f"Yandex complete {resp.status_code}: {resp.text}")
    return resp.json()["result"]["alternatives"][0]["message"]["text"]


_STT_URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"


async def transcribe_voice(
    audio_bytes: bytes, format: str = "oggopus", sample_rate_hertz: int | None = None
) -> str:
    """Распознаёт голосовое сообщение через Yandex SpeechKit v1.
    Для format="lpcm" обязателен sample_rate_hertz (8000/16000/48000)."""
    if not settings.yandex_folder_id or not settings.yandex_api_key:
        raise RuntimeError("Yandex API не настроен")
    client = _get_client()
    params = {
        "folderId": settings.yandex_folder_id,
        "lang": "ru-RU",
        "format": format,
    }
    if sample_rate_hertz is not None:
        params["sampleRateHertz"] = sample_rate_hertz
    resp = await client.post(
        _STT_URL,
        content=audio_bytes,
        params=params,
        headers={
            "Authorization": f"Api-Key {settings.yandex_api_key}",
            "Content-Type": "application/octet-stream",
        },
    )
    if not resp.is_success:
        raise RuntimeError(f"Yandex STT {resp.status_code}: {resp.text}")
    return resp.json().get("result", "")


_LRR_URL = "https://transcribe.api.cloud.yandex.net/speech/stt/v2/longRunningRecognize"
_OPERATION_URL = "https://operation.api.cloud.yandex.net/operations/{id}"
_LRR_POLL_INTERVAL = 2.0
_LRR_TIMEOUT = 100.0  # держим с запасом под proxy_read_timeout (120s) в nginx

_FMT_TO_ENCODING = {
    "oggopus": "OGG_OPUS",
    "mp3": "MP3",
    "lpcm": "LINEAR16_PCM",
}


async def transcribe_voice_long(
    audio_bytes: bytes, format: str = "oggopus", sample_rate_hertz: int | None = None
) -> str:
    """Распознаёт длинное голосовое (>1 МБ) через Yandex SpeechKit v2 longRunningRecognize.
    Файл временно загружается в Object Storage, доступ для Yandex даётся через presigned URL
    (бакет остаётся приватным), после распознавания файл удаляется."""
    if not settings.yandex_storage_bucket or not settings.yandex_storage_access_key_id:
        raise RuntimeError("Yandex Object Storage не настроен")

    key = f"stt-tmp/{uuid.uuid4().hex}.bin"
    await asyncio.to_thread(storage_service.upload, key, audio_bytes)
    try:
        uri = await asyncio.to_thread(storage_service.presigned_url, key)
        return await _long_running_recognize(uri, format, sample_rate_hertz)
    finally:
        await asyncio.to_thread(storage_service.delete, key)


async def _long_running_recognize(
    uri: str, format: str, sample_rate_hertz: int | None
) -> str:
    encoding = _FMT_TO_ENCODING.get(format, "OGG_OPUS")
    specification: dict = {"audioEncoding": encoding, "languageCode": "ru-RU"}
    if encoding == "LINEAR16_PCM":
        specification["sampleRateHertz"] = sample_rate_hertz

    client = _get_client()
    resp = await client.post(
        _LRR_URL,
        headers=_headers(),
        json={"config": {"specification": specification}, "audio": {"uri": uri}},
    )
    if not resp.is_success:
        raise RuntimeError(f"Yandex LRR {resp.status_code}: {resp.text}")
    operation_id = resp.json()["id"]

    deadline = time.monotonic() + _LRR_TIMEOUT
    while time.monotonic() < deadline:
        await asyncio.sleep(_LRR_POLL_INTERVAL)
        op_resp = await client.get(_OPERATION_URL.format(id=operation_id), headers=_headers())
        if not op_resp.is_success:
            raise RuntimeError(f"Yandex operation {op_resp.status_code}: {op_resp.text}")
        data = op_resp.json()
        if not data.get("done"):
            continue
        if "error" in data:
            raise RuntimeError(f"Yandex LRR error: {data['error']}")
        chunks = data.get("response", {}).get("chunks", [])
        parts = [
            chunk["alternatives"][0]["text"]
            for chunk in chunks
            if chunk.get("alternatives")
        ]
        return " ".join(parts)

    raise RuntimeError("Yandex LRR timeout: распознавание не завершилось вовремя")


_VISION_URL = "https://vision.api.cloud.yandex.net/vision/v1/batchAnalyze"


async def ocr_image(image_bytes: bytes) -> str:
    """Распознаёт текст на изображении (в т.ч. отсканированной странице PDF)
    через Yandex Vision OCR. Используется для файлов без текстового слоя,
    которые pypdf/python-docx не могут прочитать напрямую."""
    resp = await _get_client().post(
        _VISION_URL,
        headers=_headers(),
        json={
            "folderId": settings.yandex_folder_id,
            "analyze_specs": [{
                "content": base64.b64encode(image_bytes).decode("ascii"),
                "features": [{
                    "type": "TEXT_DETECTION",
                    "text_detection_config": {"language_codes": ["ru", "en"]},
                }],
            }],
        },
        timeout=60,
    )
    if not resp.is_success:
        raise RuntimeError(f"Yandex Vision OCR {resp.status_code}: {resp.text}")

    lines: list[str] = []
    for result in resp.json().get("results", []):
        for r in result.get("results", []):
            for page in r.get("textDetection", {}).get("pages", []):
                for block in page.get("blocks", []):
                    for line in block.get("lines", []):
                        text = line.get("text")
                        if text:
                            lines.append(text)
    return "\n".join(lines)
