import argparse
from getpass import getpass
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4


ROOT = Path(__file__).resolve().parent
MARKER = "Демонстрационный материал SAVT Assist для проверки АТС."


class DemoImportError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class SavtApi:
    def __init__(self, base_url):
        parsed = urlsplit(base_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise DemoImportError("Укажите HTTPS-адрес API без пути, параметров и учётных данных.")
        self.base_url = base_url.rstrip("/")
        self.opener = build_opener(NoRedirect())
        self.access_token = None
        self.last_request = 0.0

    def request(self, method, path, *, payload=None, attachment=None):
        headers = {"Accept": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        if attachment is not None:
            boundary = uuid4().hex
            head = (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{attachment.name}\"\r\n"
                "Content-Type: application/pdf\r\n\r\n"
            )
            body = head.encode() + attachment.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"

        # Запросы идут последовательно, с запасом относительно общего лимита Nginx.
        time.sleep(max(0.0, 0.85 - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=40) as response:
                content = response.read()
        except HTTPError as error:
            code = error.code
            error.close()
            hint = " После паузы можно повторить ту же команду." if code == 429 else ""
            raise DemoImportError(f"{method} {path}: API вернул HTTP {code}.{hint}") from None
        except (URLError, TimeoutError):
            raise DemoImportError(f"{method} {path}: не удалось получить ответ от API.") from None
        try:
            return json.loads(content) if content else None
        except ValueError:
            raise DemoImportError(f"{method} {path}: ожидался ответ JSON.") from None


def load_corpus():
    corpus = json.loads((ROOT / "corpus.json").read_text(encoding="utf-8"))
    articles = [article for category in corpus["categories"] for article in category["articles"]]
    faq = [entry for article in articles for entry in article["faq"]]
    attachments = [article["attachment"] for article in articles if "attachment" in article]
    if len(articles) != 50 or len(faq) != 150 or not attachments:
        raise DemoImportError("В пакете должны быть 50 статей, 150 вопросов и PDF-вложения.")
    if len({article["title"] for article in articles}) != len(articles):
        raise DemoImportError("В пакете повторяются заголовки статей.")
    if len({entry["question"] for entry in faq}) != len(faq):
        raise DemoImportError("В пакете повторяются вопросы FAQ.")
    for filename in attachments:
        if Path(filename).name != filename or not filename.isascii() or not filename.endswith(".pdf"):
            raise DemoImportError("Неверное имя PDF в пакете.")
        if not (ROOT / filename).read_bytes().startswith(b"%PDF-"):
            raise DemoImportError(f"Файл {filename} не является PDF.")
    return corpus, len(articles), len(faq), len(attachments)


def matching_record(rows, field, value):
    matches = [row for row in rows if row[field] == value]
    if len(matches) > 1:
        raise DemoImportError(f"Найдено несколько записей с одинаковым значением «{value}».")
    return matches[0] if matches else None


def ensure_record(api, path, existing, field, payload):
    current = matching_record(existing, field, payload[field])
    if current is not None:
        return current, False
    return api.request("POST", path, payload=payload), True


def category_items(api, path, category_id):
    page = 1
    items = []
    while True:
        query = urlencode({"category_id": category_id, "page": page, "size": 100})
        response = api.request("GET", f"{path}?{query}")
        items.extend(response["items"])
        if page >= response["pages"]:
            return items
        page += 1


def require_published(record, name):
    if not record.get("is_published"):
        raise DemoImportError(f"Запись «{name}» уже существует и снята с публикации.")


def import_corpus(api, corpus):
    kb_categories = api.request("GET", "/admin/kb/categories")
    faq_categories = api.request("GET", "/admin/faq/categories")
    counts = {"articles": 0, "faq": 0, "attachments": 0}
    for category in corpus["categories"]:
        name = f"{corpus['prefix']} {category['category']}"
        category_data = {"name": name}
        kb, _ = ensure_record(api, "/admin/kb/categories", kb_categories, "name", category_data)
        faq, _ = ensure_record(api, "/admin/faq/categories", faq_categories, "name", category_data)
        existing_articles = category_items(api, "/admin/kb/articles", kb["id"])
        existing_faq = category_items(api, "/admin/faq/entries", faq["id"])

        for article in category["articles"]:
            record, created = ensure_record(api, "/admin/kb/articles", existing_articles, "title", {
                "category_id": kb["id"], "title": article["title"],
                "description": f"{article['body']}\n\n{MARKER}",
            })
            require_published(record, article["title"])
            counts["articles"] += int(created)
            if filename := article.get("attachment"):
                detail = record if created else api.request("GET", f"/kb/articles/{record['id']}")
                if not any(attachment["title"] == filename for attachment in detail["attachments"]):
                    api.request("POST", f"/admin/kb/articles/{record['id']}/attachments", attachment=ROOT / filename)
                    counts["attachments"] += 1

            for entry in article["faq"]:
                record, created = ensure_record(api, "/admin/faq/entries", existing_faq, "question", {
                    "category_id": faq["id"], **entry,
                })
                require_published(record, entry["question"])
                counts["faq"] += int(created)
        print(f"Проверена категория {name}", flush=True)
    return counts


def main():
    parser = argparse.ArgumentParser(description="Публикация демонстрационных материалов для проверки АТС")
    parser.add_argument("--base-url", default="https://helper.savt.by")
    parser.add_argument("--login", default="admin")
    parser.add_argument("--publish", action="store_true", help="записать опубликованные материалы на сервер")
    args = parser.parse_args()

    try:
        corpus, articles, faq, attachments = load_corpus()
        print(f"Пакет проверен: {articles} статей, {faq} вопросов, {attachments} PDF.")
        if not args.publish:
            return 0
        api = SavtApi(args.base_url)
        tokens = api.request("POST", "/auth/admin-login", payload={
            "login": args.login, "password": getpass(f"Пароль {args.login} для {api.base_url}: "),
        })
        api.access_token = tokens["access_token"]
        try:
            counts = import_corpus(api, corpus)
        finally:
            try:
                api.request("POST", "/auth/logout", payload={"refresh_token": tokens["refresh_token"]})
            except DemoImportError as error:
                print(f"Завершение сессии: {error}", file=sys.stderr)
        print(f"Добавлено: {counts['articles']} статей, {counts['faq']} вопросов, {counts['attachments']} PDF.")
        print("Индексация идёт в фоне. Готовность проверьте в журнале API или тестовым вызовом АТС.")
        return 0
    except (DemoImportError, OSError, KeyError, ValueError) as error:
        print(f"Импорт остановлен: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
