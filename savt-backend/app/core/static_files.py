"""Раздача /static приложением с проверкой подписи ссылки.

Основная раздача файлов идёт через nginx (secure_link), но приложение тоже отдаёт
/static, и этот путь доступен всем, кто может обратиться к api в обход nginx
(например, через проксирование панели). Подпись и срок здесь проверяются тем же
алгоритмом, что и в nginx (см. signed_urls.verify_signature), поэтому файл без
действующей подписи не отдаётся ни одним из путей."""
from fastapi import HTTPException
from fastapi.staticfiles import StaticFiles

from app.core.signed_urls import verify_signature


class SignedStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        query = scope.get("query_string", b"").decode("latin-1")
        url = scope["path"] + (f"?{query}" if query else "")
        if not verify_signature(url):
            raise HTTPException(status_code=403, detail="Ссылка недействительна или истекла")
        return await super().get_response(path, scope)
