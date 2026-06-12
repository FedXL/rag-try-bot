# RAG Architecture

This document describes the current request flow implemented by the Telegram
RAG bot. It intentionally separates the source knowledge-base path from the
product search path because they use different retrieval strategies.

## Runtime Services

```mermaid
flowchart LR
    TG[Telegram user]
    BOT[bot<br/>aiogram polling]
    WEB[web<br/>Django DRF]
    NGINX[nginx<br/>external HTTP :9001]
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
    WEB -. optional /embed, /rerank .-> ML
    WEB --> REDIS
    BOT --> REDIS
    CELERY --> REDIS
    CELERY <--> DB
    BEAT --> REDIS
```

## Request Pipeline

```mermaid
flowchart TD
    A[Telegram message] --> B[bot.main handle]
    B --> C{User already has<br/>in-flight request?}
    C -->|yes| C1[Send busy notice]
    C -->|no| D[Build request_id and POST /api/chat/]

    D --> E[views.chat]
    E --> F[Upsert TelegramUser]
    F --> G[pipeline.answer_user_message]
    G --> H[Save inbound ChatMessage]
    H --> I[Load recent history]
    I --> J[classify_message]

    J --> J1[domain_rules classifier]
    J1 --> J2{Greeting or no LLM?}
    J2 -->|yes| K[rule result]
    J2 -->|no| J3[xAI classifier JSON]
    J3 --> K

    K --> L{need_search?}
    L -->|no| M[direct_answer]
    L -->|yes| N{Classifier class kind}

    N -->|product| P[Product branch]
    N -->|color_selection| Q[Color selection service]
    N -->|source/system| R[Source RAG branch]

    R --> R1[search.class_source_search]
    R1 --> R2[Select active Source rows<br/>for class_slug]
    R2 --> R3{Candidates found?}
    R3 -->|yes| R4[grounded_answer with source context]
    R3 -->|no| R5[Not enough info response]

    P --> P1[answer_product_message]
    P1 --> P2[Build ProductPlan]
    P2 --> P3[hybrid_product_search]
    P3 --> P4[Tool-style product answer]

    Q --> Q1[answer_color_selection_message]

    M --> S[Save assistant ChatMessage]
    R4 --> S
    R5 --> S
    P4 --> S
    Q1 --> S
    S --> T[Return answer to bot]
    T --> U[Send Telegram response]
```

## Product Hybrid Search

```mermaid
flowchart TD
    A[Product query] --> B[Build ProductPlan]
    B --> C{Exact SKU lookup?}
    C -->|hit| D[Return exact products]
    C -->|miss or no SKU| E[Lexical product search]
    E --> F[Trigram product search]
    F --> G[Optional vector search]
    G --> G1[POST ml-api /embed]
    G1 --> G2[ORDER BY vector distance]
    G2 --> H[Merge and de-duplicate]
    H --> I[Sort by product_score]
    I --> J[Optional rerank]
    J --> J1[POST ml-api /rerank]
    J1 --> K[Top products]
    K --> L[Answer product intent]
```

## Current Implementation Notes

- The public Telegram path is `bot.main` -> `POST /api/chat/` -> `views.chat` ->
  `pipeline.answer_user_message`.
- The classifier first runs local domain rules. If the message is not a simple
  bypass case and `XAI_API_KEY` is configured, it asks the xAI chat model for a
  JSON classification.
- Source knowledge-base answers use `ClassifierClass` and active `Source` rows.
  `search.search()` currently routes non-product searches to
  `class_source_search()`.
- `quick_phrase_search()` exists in `backend/app/core/search.py`, but the current
  `search()` function does not call it.
- Source/article embeddings are disabled in the current core search path:
  `removed_embedding_status()` returns `status="removed"`, and
  `POST /api/index/prepare/` returns `{"status": "skipped",
  "reason": "embeddings_removed"}`.
- Product retrieval is separate from source RAG. It can use exact SKU lookup,
  lexical search, trigram search, optional pgvector search through `ml-api
  /embed`, and optional reranking through `ml-api /rerank`.
- Postgres is both the application database and the retrieval store. Redis is
  used for Celery and bot-side user locks.
