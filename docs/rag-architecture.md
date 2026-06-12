# Архитектура RAG

Этот документ описывает текущий путь запроса в Telegram RAG-боте. Схема
отдельно показывает обычный поиск по базе знаний и продуктовый поиск, потому
что в коде они используют разные стратегии поиска.

## Сервисы

```mermaid
flowchart LR
    TG[Пользователь Telegram]
    BOT[bot<br/>aiogram polling]
    WEB[web<br/>Django DRF]
    NGINX[nginx<br/>внешний HTTP :9001]
    DB[(Postgres<br/>pg_trgm + pgvector)]
    REDIS[(Redis)]
    CELERY[celery worker]
    BEAT[celery beat]
    ML[ml-api<br/>FastAPI embed/rerank]
    XAI[xAI Chat API]

    TG <--> BOT
    BOT -->|POST /api/chat/| WEB
    NGINX --> WEB
    WEB <--> DB
    WEB <--> XAI
    WEB -. опционально /embed, /rerank .-> ML
    WEB --> REDIS
    BOT --> REDIS
    CELERY --> REDIS
    CELERY <--> DB
    BEAT --> REDIS
```

## Pipeline запроса

```mermaid
flowchart TD
    A[Сообщение в Telegram] --> B[bot.main handle]
    B --> C{У пользователя уже есть<br/>запрос в обработке?}
    C -->|да| C1[Отправить сообщение о занятости]
    C -->|нет| D[Собрать request_id и POST /api/chat/]

    D --> E[views.chat]
    E --> F[Создать или обновить TelegramUser]
    F --> G[pipeline.answer_user_message]
    G --> H[Сохранить входящий ChatMessage]
    H --> I[Загрузить недавнюю историю]
    I --> J[classify_message]

    J --> J1[domain_rules classifier]
    J1 --> J2{Приветствие<br/>или LLM недоступна?}
    J2 -->|да| K[Результат правил]
    J2 -->|нет| J3[xAI classifier JSON]
    J3 --> K

    K --> L{need_search?}
    L -->|нет| M[direct_answer]
    L -->|да| N{Тип класса классификатора}

    N -->|product| P[Продуктовая ветка]
    N -->|color_selection| Q[Сервис подбора цвета]
    N -->|source/system| R[Source RAG ветка]

    R --> R1[search.class_source_search]
    R1 --> R2[Выбрать активные Source<br/>для class_slug]
    R2 --> R3{Кандидаты найдены?}
    R3 -->|да| R4[grounded_answer по найденному контексту]
    R3 -->|нет| R5[Ответ: недостаточно информации]

    P --> P1[answer_product_message]
    P1 --> P2[Собрать ProductPlan]
    P2 --> P3[hybrid_product_search]
    P3 --> P4[Ответ продуктового инструмента]

    Q --> Q1[answer_color_selection_message]

    M --> S[Сохранить assistant ChatMessage]
    R4 --> S
    R5 --> S
    P4 --> S
    Q1 --> S
    S --> T[Вернуть ответ в bot]
    T --> U[Отправить ответ в Telegram]
```

## Гибридный поиск по товарам

```mermaid
flowchart TD
    A[Запрос по товарам] --> B[Собрать ProductPlan]
    B --> C{Точный поиск по SKU?}
    C -->|найдено| D[Вернуть точные товары]
    C -->|не найдено или нет SKU| E[Лексический поиск товаров]
    E --> F[Trigram поиск товаров]
    F --> G[Опциональный векторный поиск]
    G --> G1[POST ml-api /embed]
    G1 --> G2[ORDER BY vector distance]
    G2 --> H[Объединить и убрать дубли]
    H --> I[Отсортировать по product_score]
    I --> J[Опциональный rerank]
    J --> J1[POST ml-api /rerank]
    J1 --> K[Лучшие товары]
    K --> L[Ответить на продуктовый intent]
```

## Заметки по текущей реализации

- Публичный Telegram-путь: `bot.main` -> `POST /api/chat/` -> `views.chat` ->
  `pipeline.answer_user_message`.
- Классификатор сначала запускает локальные domain rules. Если сообщение не
  является простым bypass-сценарием и задан `XAI_API_KEY`, он запрашивает у xAI
  JSON-классификацию.
- Ответы по обычной базе знаний используют `ClassifierClass` и активные строки
  `Source`. Сейчас `search.search()` направляет не-продуктовые запросы в
  `class_source_search()`.
- `quick_phrase_search()` есть в `backend/app/core/search.py`, но текущая
  функция `search()` его не вызывает.
- Embeddings для source/article в текущем core search отключены:
  `removed_embedding_status()` возвращает `status="removed"`, а
  `POST /api/index/prepare/` возвращает `{"status": "skipped",
  "reason": "embeddings_removed"}`.
- Поиск по товарам отделен от обычного source RAG. Он может использовать
  точный поиск по SKU, lexical search, trigram search, опциональный pgvector
  search через `ml-api /embed` и опциональный rerank через `ml-api /rerank`.
- Postgres используется и как основная база приложения, и как retrieval store.
  Redis используется для Celery и пользовательских lock-ов в боте.
