import httpx

from app.config import settings

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
    """Эмбеддинг для индексируемых документов (без кэша — каждый чанк уникален)."""
    return await _embed(text, "text-search-doc")


async def embed_query(text: str) -> list[float]:
    """Эмбеддинг поискового запроса с кэшированием."""
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
    """Генерация ответа YandexGPT."""
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
