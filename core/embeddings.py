#!/usr/bin/env python3
"""Nomic embedding client and SQLite vector store for Kuza."""
from __future__ import annotations
import json
import math
import sqlite3
import struct
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from utils.config import EMBED_SERVER_PORT, KUZA_STATE_DIR
from utils.logger import error

EMBEDDING_MODEL = "nomic-embed-text-v1.5"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
_VECTOR_MAGIC = b"KZV1"
_MAX_VECTOR_DIMENSIONS = 4096

def _encode_embedding(vector) -> bytes:
    values = [float(value) for value in vector]
    if not 0 < len(values) <= _MAX_VECTOR_DIMENSIONS:
        raise ValueError("Embedding has an invalid dimension")
    return _VECTOR_MAGIC + struct.pack("<I", len(values)) + struct.pack(f"<{len(values)}f", *values)

def _decode_embedding(blob: bytes) -> list[float] | None:
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        return None
    raw = bytes(blob)
    if len(raw) < 8 or not raw.startswith(_VECTOR_MAGIC):
        return None
    dimensions = struct.unpack("<I", raw[4:8])[0]
    if not 0 < dimensions <= _MAX_VECTOR_DIMENSIONS:
        return None
    payload = raw[8:]
    if len(payload) != dimensions * 4:
        return None
    return list(struct.unpack(f"<{dimensions}f", payload))

@dataclass
class Embedding:
    id: int
    file_path: str
    chunk_start: int
    chunk_end: int
    embedding: bytes
    created_at: int

class EmbeddingModel:
    def __init__(self, model_name: str = EMBEDDING_MODEL, port: int = EMBED_SERVER_PORT):
        self.model_name = model_name
        self.port = int(port)
        self.url = f"http://127.0.0.1:{self.port}/v1/embeddings"
        self._loaded = False
    def _request(self, texts: List[str]) -> Optional[List[bytes]]:
        if not texts:
            return []
        request = urllib.request.Request(
            self.url,
            data=json.dumps({"model": "local", "input": [str(text) for text in texts]}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
            rows = sorted(payload.get("data", []), key=lambda row: row["index"])
            if len(rows) != len(texts):
                return None
            encoded = [_encode_embedding(row["embedding"]) for row in rows]
            self._loaded = True
            return encoded
        except (OSError, ValueError, KeyError, urllib.error.URLError) as exc:
            error(f"Embedding request failed: {exc}")
            self._loaded = False
            return None
    def embed(self, text: str) -> Optional[bytes]:
        result = self._request([text])
        return result[0] if result else None
    def embed_batch(self, texts: List[str]) -> Optional[List[bytes]]:
        return self._request(texts)
    def is_loaded(self) -> bool:
        return self._loaded

class EmbeddingStore:
    def __init__(self, db_path: Path = None):
        if db_path is None:
            KUZA_STATE_DIR.mkdir(parents=True, exist_ok=True)
            db_path = KUZA_STATE_DIR / "state.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()
        try:
            self.db_path.parent.chmod(0o700)
            self.db_path.chmod(0o600)
        except OSError:
            pass
    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn
    def _ensure_schema(self):
        with self._get_connection() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS longterm_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                chunk_start INTEGER NOT NULL,
                chunk_end INTEGER NOT NULL,
                embedding BLOB NOT NULL,
                created_at INTEGER NOT NULL
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_file_path ON longterm_embeddings(file_path)")
    def store(self, file_path: str, chunk_start: int, chunk_end: int, embedding: bytes) -> int:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO longterm_embeddings (file_path, chunk_start, chunk_end, embedding, created_at) VALUES (?, ?, ?, ?, ?)",
                (file_path, chunk_start, chunk_end, embedding, int(time.time())),
            )
            return int(cursor.lastrowid)
    def store_batch(self, embeddings: List[Tuple[str, int, int, bytes]]) -> int:
        now = int(time.time())
        rows = [(path, start, end, vector, now) for path, start, end, vector in embeddings]
        if not rows:
            return 0
        with self._get_connection() as conn:
            conn.executemany(
                "INSERT INTO longterm_embeddings (file_path, chunk_start, chunk_end, embedding, created_at) VALUES (?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)
    def search(self, query_embedding: bytes, limit: int = 5) -> List[Dict]:
        query = _decode_embedding(query_embedding)
        if query is None:
            return []
        query_norm = math.sqrt(sum(value * value for value in query))
        if query_norm == 0:
            return []
        with self._get_connection() as conn:
            rows = conn.execute("SELECT id, file_path, chunk_start, chunk_end, embedding, created_at FROM longterm_embeddings").fetchall()
        scored = []
        for row in rows:
            vector = _decode_embedding(row["embedding"])
            if vector is None or len(vector) != len(query):
                continue
            norm = math.sqrt(sum(value * value for value in vector))
            if norm == 0:
                continue
            score = sum(a * b for a, b in zip(query, vector)) / (query_norm * norm)
            scored.append((score, {"id": row["id"], "file_path": row["file_path"], "chunk_start": row["chunk_start"], "chunk_end": row["chunk_end"], "created_at": row["created_at"], "similarity": round(score, 4)}))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [item for _, item in scored[:max(1, int(limit))]]
    def get_by_file(self, file_path: str) -> List[Dict]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT id, file_path, chunk_start, chunk_end, created_at FROM longterm_embeddings WHERE file_path = ? ORDER BY chunk_start", (file_path,)).fetchall()
        return [dict(row) for row in rows]
    def delete_by_file(self, file_path: str) -> int:
        with self._get_connection() as conn:
            return int(conn.execute("DELETE FROM longterm_embeddings WHERE file_path = ?", (file_path,)).rowcount)
    def count(self) -> int:
        with self._get_connection() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM longterm_embeddings").fetchone()[0])

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[Tuple[str, int, int]]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    overlap = max(0, min(overlap, chunk_size - 1))
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append((text[start:end], start, end))
        if end == len(text):
            break
        start = end - overlap
    return chunks

_embedding_model: Optional[EmbeddingModel] = None
_embedding_store: Optional[EmbeddingStore] = None

def get_embedding_model() -> EmbeddingModel:
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = EmbeddingModel()
    return _embedding_model

def get_embedding_store() -> EmbeddingStore:
    global _embedding_store
    if _embedding_store is None:
        _embedding_store = EmbeddingStore()
    return _embedding_store

def reset_embeddings():
    global _embedding_model, _embedding_store
    _embedding_model = None
    _embedding_store = None
