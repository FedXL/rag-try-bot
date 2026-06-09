# Telegram RAG Bot

Русскоязычный Telegram RAG-бот на Django, Celery, Postgres/pgvector и отдельном FastAPI ML-сервисе.

## Архитектура

- `bot` - aiogram polling-бот. Принимает сообщения Telegram и отправляет их во внутренний Django API.
- `web` - Django/DRF backend и Django Admin. Наружу публикуется порт `22972 -> 9001`.
- `celery` - worker для фоновых задач, включая построение эмбеддингов.
- `beat` - Celery Beat.
- `ml-api` - FastAPI-сервис для ML-задач. Держит embedding/reranker модели в памяти и считает на GPU при `ML_DEVICE=cuda`.
- `db` - Postgres с расширением pgvector. Используется и как основная БД, и как векторное хранилище.
- `redis` - broker/result backend для Celery.

## Pipeline запроса

1. Пользователь пишет сообщение в Telegram.
2. `bot` отправляет запрос в `web` на `/api/chat/`.
3. Django сохраняет пользователя и входящее сообщение.
4. Классификатор решает, нужен ли поиск по базе знаний.
5. Если поиск не нужен, Django отвечает напрямую через LLM или fallback-логику.
6. Если поиск нужен, Django ищет кандидатов в Postgres:
   - lexical search через `pg_trgm`;
   - vector search через `pgvector`, если эмбеддинги уже построены.
7. Django отправляет кандидатов в `ml-api` для rerank.
8. LLM формирует ответ только по найденному контексту.
9. Ответ сохраняется в истории и отправляется пользователю.

## Требования

- Docker и Docker Compose.
- Для GPU-режима: NVIDIA driver, NVIDIA Container Toolkit и Docker с поддержкой `--gpus all`.
- Для CPU-режима можно поставить `ML_DEVICE=cpu`, но обработка будет медленнее.

Проверка GPU внутри Docker:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

## Настройка

Создайте `.env` из примера:

```bash
cp .env.example .env
```

Заполните обязательные переменные:

- `TELEGRAM_BOT_TOKEN`
- `DJANGO_SECRET_KEY`
- `POSTGRES_PASSWORD`
- `DATABASE_URL`
- `INTERNAL_API_TOKEN`
- `XAI_API_KEY`, если нужен внешний LLM для классификации и генерации

Без `XAI_API_KEY` бот продолжит работать, но будет использовать fallback-классификатор и ответы только из найденных записей базы знаний.

## Запуск

```bash
docker compose up -d --build
```

Проверка:

```bash
docker compose ps
curl http://127.0.0.1:22972/api/health/
docker exec telegram-rag-bot-ml-api-1 python -c "import torch; print(torch.__version__); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu')"
```

Админка:

```text
http://SERVER_IP:22972/admin/
```

Логин и пароль задаются переменными:

```text
DJANGO_SUPERUSER_USERNAME
DJANGO_SUPERUSER_PASSWORD
```

## Работа с базой знаний

В Django Admin добавьте записи `QAItem`:

- `source_number` - уникальный номер записи;
- `question_ru` - вопрос на русском;
- `answer_ru` - ответ на русском.

После добавления данных запустите построение эмбеддингов:

```bash
curl -X POST http://127.0.0.1:22972/api/index/prepare/
```

Статус можно смотреть через:

```bash
curl http://127.0.0.1:22972/api/health/
```

## API

- `GET /api/health/` - состояние сервиса и индекса.
- `POST /api/chat/` - внутренний endpoint для Telegram bot.
- `POST /api/search/` - ручной поиск по базе знаний.
- `POST /api/index/prepare/` - запуск построения эмбеддингов.
- `GET /api/tasks/<task_id>/` - статус Celery-задачи.
- `GET /api/questions/` - список вопросов.

## Безопасность

Не коммитьте `.env`. В репозитории должен лежать только `.env.example`.

`/api/chat/` защищается заголовком `X-Internal-Api-Token`, если задан `INTERNAL_API_TOKEN`.
