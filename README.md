# Telegram RAG Bot

Русскоязычный Telegram RAG-бот на Django, aiogram, Celery, Postgres/pgvector и отдельном FastAPI ML-сервисе.

## Архитектура

- `bot` - aiogram polling-бот. Принимает сообщения Telegram и отправляет их во внутренний Django API.
- `web` - Django/DRF backend и Django Admin.
- `nginx` - внешняя точка входа на порт `9001`, проксирует Django и раздает `/static/`.
- `celery` - worker для фоновых задач, включая построение эмбеддингов.
- `beat` - Celery Beat.
- `ml-api` - FastAPI-сервис для embedding/rerank моделей. Модели держатся в памяти процесса.
- `db` - Postgres с `pgvector` и `pg_trgm`. Используется как основная БД и векторное хранилище.
- `redis` - broker/result backend для Celery.

## База знаний

База знаний разделена на две основные таблицы:

- `QuickPhrase` - быстрые фразы/вопросы пользователя. Они не чанкуются и не эмбеддятся. Каждая фраза ведет на `article_key`.
- `ArticleChunk` - чанки статей, страниц и каталога. В этой же таблице хранится колонка `embedding vector`.

Старые `QAItem` не удалены для совместимости. Команда `sync_initial_knowledge` переносит их в `ArticleChunk(section=catalog)`.

## Pipeline запроса

1. Пользователь пишет сообщение в Telegram.
2. `bot` отправляет запрос в `web` на `/api/chat/`.
3. Django сохраняет пользователя и входящее сообщение.
4. Классификатор делает два шага:
   - решает, нужен ли поиск в базе знаний;
   - выбирает раздел: `catalog`, `help`, `about`, `inspiration`, `color_selection`, `partners`, `glossary`, `contacts`, `news_articles`, `mixed`, `none`.
5. Если поиск не нужен, ответ формируется напрямую.
6. Если поиск нужен, Django сначала ищет в `QuickPhrase`.
7. Если быстрая фраза найдена, берется связанный `ArticleChunk`.
8. Если быстрой фразы нет, Django ищет по `ArticleChunk`:
   - lexical search через `pg_trgm`;
   - vector search через `pgvector`, если индекс готов.
9. Кандидаты отправляются в `ml-api` на rerank.
10. LLM формирует ответ только по найденному контексту.
11. Ответ сохраняется в истории и отправляется пользователю.

## Настройка

```bash
cp .env.example .env
```

Обязательные переменные:

- `TELEGRAM_BOT_TOKEN`
- `DJANGO_SECRET_KEY`
- `POSTGRES_PASSWORD`
- `DATABASE_URL`
- `INTERNAL_API_TOKEN`
- `XAI_API_KEY`, если нужен LLM-классификатор и генерация ответов

Админ создается через:

```text
DJANGO_SUPERUSER_USERNAME=admin
DJANGO_SUPERUSER_PASSWORD=admin
```

## Запуск

```bash
docker compose up -d --build
```

Проверка:

```bash
docker compose ps
curl http://127.0.0.1:9001/api/health/
```

Админка:

```text
http://SERVER_IP:9001/admin/
```

Если провайдер пробрасывает внешний порт на серверный `9001`, используйте внешний порт провайдера. Например: `PUBLIC_IP:22972 -> server:9001`.

## Индексация

Начальные данные и legacy `QAItem` синхронизируются командой:

```bash
docker compose exec web python manage.py sync_initial_knowledge
```

Построение эмбеддингов для `ArticleChunk`:

```bash
curl -X POST http://127.0.0.1:9001/api/index/prepare/
```

Статус:

```bash
curl http://127.0.0.1:9001/api/health/
```

## API

- `GET /api/health/` - состояние сервиса и индекса.
- `POST /api/chat/` - внутренний endpoint для Telegram bot.
- `POST /api/search/` - ручной поиск по базе знаний.
- `POST /api/index/prepare/` - запуск построения эмбеддингов.
- `GET /api/tasks/<task_id>/` - статус Celery-задачи.
- `GET /api/questions/` - legacy список `QAItem`.

## Логи

Запрос размечается единым `request_id`.

```bash
docker compose logs -f bot web celery ml-api
```

Ключевые стадии:

```text
stage=telegram->bot event=message_received
stage=bot->django event=chat_post
stage=bot->django event=chat_received
stage=classifier event=classified
stage=pipeline event=route_selected
stage=search event=quick_phrase_hit
stage=search event=article_lexical_done
stage=search event=article_vector_done
stage=django->ml-api event=request
stage=pipeline event=done
stage=bot->telegram event=answer_sent
```

## Безопасность

Не коммитьте `.env`. В репозитории должен лежать только `.env.example`.

`/api/chat/` защищается заголовком `X-Internal-Api-Token`, если задан `INTERNAL_API_TOKEN`.
