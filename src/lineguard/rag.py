from __future__ import annotations

import hashlib
import math
import re
from typing import Any

import chromadb
from pypdf import PdfReader

from lineguard.audit import audited_tool
from lineguard.config import get_settings
from lineguard.models import KnowledgeSource
from lineguard.repository import get_assets, update_asset

COLLECTION_NAME = "lineguard-standards"
EMBEDDING_DIMENSION = 256


def _tokens(text: str) -> list[str]:
    ascii_words = re.findall(r"[a-zA-Z0-9_.-]+", text.lower())
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    chinese_tokens = list(chinese) + [chinese[i : i + 2] for i in range(len(chinese) - 1)]
    return ascii_words + chinese_tokens


def _embedding(text: str) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSION
    for token in _tokens(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSION
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _collection():
    client = chromadb.PersistentClient(path=str(get_settings().chroma_dir))
    return client.get_or_create_collection(
        COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def _chunks(text: str, size: int = 800, overlap: int = 120) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    chunks = []
    start = 0
    while start < len(normalized):
        end = min(start + size, len(normalized))
        chunks.append(normalized[start:end])
        if end == len(normalized):
            break
        start = max(start + 1, end - overlap)
    return chunks


def index_pdf_asset(asset_id: str) -> dict[str, Any]:
    assets = get_assets([asset_id])
    if not assets:
        raise KeyError(asset_id)
    asset = assets[0]
    reader = PdfReader(asset.storage_path)
    collection = _collection()
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, Any]] = []
    embeddings: list[list[float]] = []

    for page_index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        page_chunks = _chunks(text)
        for chunk_index, chunk in enumerate(page_chunks):
            chunk_id = f"{asset.id}:{page_index}:{chunk_index}"
            ids.append(chunk_id)
            documents.append(chunk)
            first_line = chunk.split("。", 1)[0][:120]
            metadatas.append(
                {
                    "asset_id": asset.id,
                    "document_name": asset.filename,
                    "page": page_index,
                    "section": first_line,
                }
            )
            embeddings.append(_embedding(chunk))

    if not documents:
        update_asset(asset_id, status="OCR_REQUIRED")
        return {"asset_id": asset_id, "status": "OCR_REQUIRED", "chunks": 0}

    collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=embeddings,
    )
    update_asset(asset_id, status="indexed")
    return {"asset_id": asset_id, "status": "indexed", "chunks": len(documents)}


@audited_tool("retrieve_powerline_standards", max_retries=1)
def retrieve_powerline_standards(
    task_id: str,
    query: str,
    asset_ids: list[str],
    limit: int = 5,
) -> list[KnowledgeSource]:
    pdf_ids = {asset.id for asset in get_assets(asset_ids) if asset.asset_type == "pdf"}
    if not pdf_ids:
        return []
    collection = _collection()
    if collection.count() == 0:
        return []
    result = collection.query(
        query_embeddings=[_embedding(query)],
        n_results=min(limit, collection.count()),
        where={"asset_id": {"$in": sorted(pdf_ids)}},
        include=["documents", "metadatas", "distances"],
    )
    sources: list[KnowledgeSource] = []
    ids = result.get("ids", [[]])[0]
    documents = result.get("documents", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]
    for source_id, document, metadata, distance in zip(
        ids, documents, metadatas, distances, strict=False
    ):
        score = round(max(0.0, 1.0 - float(distance)), 4)
        if score <= 0:
            continue
        sources.append(
            KnowledgeSource(
                source_id=source_id,
                document_name=metadata.get("document_name", "unknown"),
                page=metadata.get("page"),
                section=metadata.get("section"),
                excerpt=document[:500],
                score=score,
            )
        )
        if len(sources) >= limit:
            break
    return sources
