import re


def normalize_text(text: str) -> str:
    value = (text or "").lower().replace("ё", "е")
    value = re.sub(r"[^0-9a-zа-я]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def chunk_text(text: str, max_chars: int = 1200, overlap: int = 120) -> list[str]:
    value = re.sub(r"\s+", " ", (text or "").strip())
    if not value:
        return []
    if len(value) <= max_chars:
        return [value]

    chunks: list[str] = []
    start = 0
    while start < len(value):
        end = min(start + max_chars, len(value))
        if end < len(value):
            split_at = value.rfind(". ", start, end)
            if split_at > start + max_chars // 2:
                end = split_at + 1
        chunks.append(value[start:end].strip())
        if end >= len(value):
            break
        start = max(0, end - overlap)
    return chunks
