import os
from typing import Any

import torch
from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import CrossEncoder, SentenceTransformer

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-m3")
RERANKER_MODEL = os.environ.get("RERANKER_MODEL", "BAAI/bge-reranker-base")
REQUESTED_DEVICE = os.environ.get("ML_DEVICE", "cpu")

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
        _embedder = SentenceTransformer(EMBEDDING_MODEL, device=_device)
    return _embedder


def reranker() -> CrossEncoder:
    global _reranker, _device
    if _reranker is None:
        _device = resolve_device()
        _reranker = CrossEncoder(RERANKER_MODEL, device=_device)
    return _reranker


@app.on_event("startup")
def startup() -> None:
    embedder().encode(["warmup text"], normalize_embeddings=True, show_progress_bar=False)
    reranker().predict([("query", "candidate")])


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
def embed(payload: EmbedRequest) -> dict[str, Any]:
    vectors = embedder().encode(payload.texts, normalize_embeddings=True, show_progress_bar=False)
    return {"vectors": [vector.tolist() for vector in vectors]}


@app.post("/rerank")
def rerank(payload: RerankRequest) -> dict[str, Any]:
    if not payload.candidates:
        return {"candidates": []}
    pairs = [(payload.query, str(item.get("question") or "")) for item in payload.candidates]
    scores = reranker().predict(pairs)
    candidates = []
    for item, score in zip(payload.candidates, scores):
        enriched = dict(item)
        enriched["reranker_score"] = float(score)
        candidates.append(enriched)
    candidates.sort(key=lambda item: item.get("reranker_score", 0.0), reverse=True)
    return {"candidates": candidates}