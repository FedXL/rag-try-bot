import logging
import os
from time import perf_counter
from typing import Annotated, Any

import torch
from fastapi import FastAPI, Header
from pydantic import BaseModel
from sentence_transformers import CrossEncoder, SentenceTransformer

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-m3")
RERANKER_MODEL = os.environ.get("RERANKER_MODEL", "BAAI/bge-reranker-base")
REQUESTED_DEVICE = os.environ.get("ML_DEVICE", "cpu")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="telegram-rag-ml-api")
_embedder: SentenceTransformer | None = None
_reranker: CrossEncoder | None = None
_device = "cpu"


class EmbedRequest(BaseModel):
    texts: list[str]


class RerankRequest(BaseModel):
    query: str
    candidates: list[dict[str, Any]]


def resolve_device() -> str:
    return "cuda" if REQUESTED_DEVICE == "cuda" and torch.cuda.is_available() else "cpu"


def runtime_info() -> dict[str, Any]:
    if not torch.cuda.is_available():
        return {"cuda_available": False}
    index = torch.cuda.current_device()
    free, total = torch.cuda.mem_get_info(index)
    return {
        "cuda_available": True,
        "device": torch.cuda.get_device_name(index),
        "memory_total_mb": round(total / 1024 / 1024),
        "memory_free_mb": round(free / 1024 / 1024),
    }


def embedder() -> SentenceTransformer:
    global _embedder, _device
    if _embedder is None:
        _device = resolve_device()
        logger.info("request_id=- stage=ml-api event=embedder_loading model=%s device=%s", EMBEDDING_MODEL, _device)
        _embedder = SentenceTransformer(EMBEDDING_MODEL, device=_device)
        logger.info("request_id=- stage=ml-api event=embedder_loaded model=%s device=%s runtime=%s", EMBEDDING_MODEL, _device, runtime_info())
    return _embedder


def reranker() -> CrossEncoder:
    global _reranker, _device
    if _reranker is None:
        _device = resolve_device()
        logger.info("request_id=- stage=ml-api event=reranker_loading model=%s device=%s", RERANKER_MODEL, _device)
        _reranker = CrossEncoder(RERANKER_MODEL, device=_device)
        logger.info("request_id=- stage=ml-api event=reranker_loaded model=%s device=%s runtime=%s", RERANKER_MODEL, _device, runtime_info())
    return _reranker


@app.on_event("startup")
def startup() -> None:
    logger.info("request_id=- stage=ml-api event=startup_begin requested_device=%s", REQUESTED_DEVICE)
    started = perf_counter()
    embedder().encode(["warmup text"], normalize_embeddings=True, show_progress_bar=False)
    reranker().predict([("query", "candidate")])
    logger.info("request_id=- stage=ml-api event=startup_complete duration_ms=%s runtime=%s", round((perf_counter() - started) * 1000), runtime_info())


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "device": _device,
        "embedding_model": EMBEDDING_MODEL,
        "reranker_model": RERANKER_MODEL,
        "runtime": runtime_info(),
        "embedder_loaded": _embedder is not None,
        "reranker_loaded": _reranker is not None,
    }


@app.post("/warmup")
def warmup() -> dict[str, Any]:
    startup()
    return health()


@app.post("/embed")
def embed(payload: EmbedRequest, x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None) -> dict[str, Any]:
    request_id = x_request_id or "-"
    started = perf_counter()
    logger.info("request_id=%s stage=ml-api event=embed_start texts=%s device=%s", request_id, len(payload.texts), _device)
    vectors = embedder().encode(payload.texts, normalize_embeddings=True, show_progress_bar=False)
    logger.info(
        "request_id=%s stage=ml-api event=embed_done vectors=%s duration_ms=%s runtime=%s",
        request_id,
        len(vectors),
        round((perf_counter() - started) * 1000),
        runtime_info(),
    )
    return {"vectors": [vector.tolist() for vector in vectors]}


@app.post("/rerank")
def rerank(payload: RerankRequest, x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None) -> dict[str, Any]:
    request_id = x_request_id or "-"
    started = perf_counter()
    if not payload.candidates:
        logger.info("request_id=%s stage=ml-api event=rerank_skipped candidates=0", request_id)
        return {"candidates": []}
    logger.info("request_id=%s stage=ml-api event=rerank_start candidates=%s device=%s", request_id, len(payload.candidates), _device)
    pairs = [(payload.query, str(item.get("question") or "")) for item in payload.candidates]
    scores = reranker().predict(pairs)
    candidates = []
    for item, score in zip(payload.candidates, scores):
        enriched = dict(item)
        enriched["reranker_score"] = float(score)
        candidates.append(enriched)
    candidates.sort(key=lambda item: item.get("reranker_score", 0.0), reverse=True)
    logger.info(
        "request_id=%s stage=ml-api event=rerank_done candidates=%s top_id=%s top_score=%.4f duration_ms=%s runtime=%s",
        request_id,
        len(candidates),
        candidates[0].get("id") if candidates else None,
        float(candidates[0].get("reranker_score", 0.0)) if candidates else 0.0,
        round((perf_counter() - started) * 1000),
        runtime_info(),
    )
    return {"candidates": candidates}
