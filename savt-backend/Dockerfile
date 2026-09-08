FROM python:3.12-slim AS builder

WORKDIR /code

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim

# libreoffice-writer — конвертация старых .doc/.xls в .docx/.xlsx при
# индексации документов ботом (см. app/services/bot_indexer.py), сами эти
# бинарные форматы python-docx/openpyxl не читают.
# libreoffice-draw — конвертация EMF/WMF (векторные схемы, вставленные в Word
# "как рисунок") в PNG перед анализом vision-моделью: Pillow такие форматы
# не открывает вообще, а именно draw-фильтры LibreOffice умеют их читать.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libreoffice-writer libreoffice-draw \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /code

COPY --from=builder /install /usr/local

COPY ./app /code/app
COPY ./alembic /code/alembic
COPY ./alembic.ini /code/alembic.ini

EXPOSE 8000

# Без --reload: прод-режим. Для разработки reload включается
# через docker-compose.dev.yml (см. README).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
