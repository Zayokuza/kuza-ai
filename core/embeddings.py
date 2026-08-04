#!/usr/bin/env python3
"""
Embeddings for Kuza-v2 hierarchical memory.

Uses sentence-transformers for semantic search:
- Embed text chunks into vectors
- Store in SQLite for persistence
- Search by semantic similarity

Model: all-MiniLM-L6-v2 (small, ~80MB, fast)
"""

import sqlite3
import struct
import time
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

from utils.logger import info, warning, error, success
from utils.config import KUZA_DIR, KUZA_STATE_DIR

# Embedding model configuration
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384  # Dimension of all-MiniLM-L6-v2
CHUNK_SIZE = 500     # Characters per chunk
CHUNK_OVERLAP = 50   # Overlap between chunks
_VECTOR_MAGIC = b"KZV1"
_MAX_VECTOR_DIMENSIONS = 4096


def _encode_embedding(vector) -> bytes:
    """Encode a numeric vector without Python object deserialization."""
    import numpy as np

    array = np.asarray(vector, dtype="<f4").reshape(-1)
    if not 0 < array.size <= _MAX_VECTOR_DIMENSIONS:
        raise ValueError("Embedding has an invalid dimension")
    return _VECTOR_MAGIC + struct.pack("<I", array.size) + array.tobytes()


def _decode_embedding(blob: bytes):
    """Decode Kuza's fixed binary vector format; reject legacy pickles."""
    import numpy as np

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
    return np.frombuffer(payload, dtype="<f4").copy()


@dataclass
class Embedding:
    """Represents a text embedding."""
    id: int
    file_path: str
    chunk_start: int
    chunk_end: int
    embedding: bytes  # Fixed-format little-endian float32 vector
    created_at: int


class EmbeddingModel:
    """
    Manages sentence-transformers embedding model.
    
    Lazy-loads the model on first use to avoid
    loading overhead when embeddings aren't needed.
    """
    
    def __init__(self, model_name: str = EMBEDDING_MODEL):
        self.model_name = model_name
        self._model = None
        self._loaded = False
    
    def _load_model(self):
        """Load the embedding model (lazy)."""
        if self._loaded:
            return
        
        try:
            from sentence_transformers import SentenceTransformer
            info(f"Loading embedding model: {self.model_name}")
            self._model = SentenceTransformer(self.model_name)
            self._loaded = True
            success(f"Embedding model loaded ({EMBEDDING_DIM} dimensions)")
        except ImportError:
            error("sentence-transformers not installed. Run: pip install sentence-transformers")
            self._loaded = False
        except Exception as e:
            error(f"Failed to load embedding model: {e}")
            self._loaded = False
    
    def embed(self, text: str) -> Optional[bytes]:
        """
        Generate embedding for text.
        
        Args:
            text: Text to embed
            
        Returns:
            Pickled numpy array of embedding, or None on error
        """
        self._load_model()
        
        if not self._loaded or self._model is None:
            return None
        
        try:
            import numpy as np
            embedding = self._model.encode(text, convert_to_numpy=True)
            return _encode_embedding(embedding)
        except Exception as e:
            error(f"Embedding error: {e}")
            return None
    
    def embed_batch(self, texts: List[str]) -> Optional[List[bytes]]:
        """
        Generate embeddings for multiple texts.
        
        Args:
            texts: List of texts to embed
            
        Returns:
            List of pickled embeddings, or None on error
        """
        self._load_model()
        
        if not self._loaded or self._model is None:
            return None
        
        try:
            import numpy as np
            embeddings = self._model.encode(texts, convert_to_numpy=True)
            return [_encode_embedding(e) for e in embeddings]
        except Exception as e:
            error(f"Batch embedding error: {e}")
            return None
    
    def is_loaded(self) -> bool:
        """Check if model is loaded."""
        return self._loaded


class EmbeddingStore:
    """
    SQLite-backed storage for embeddings.
    
    Stores:
    - File path
    - Chunk positions
    - Embedding vector (validated float32 binary format)
    - Timestamp
    """
    
    def __init__(self, db_path: Path = None):
        if db_path is None:
            db_dir = KUZA_STATE_DIR
            db_dir.mkdir(parents=True, exist_ok=True)
            try:
                db_dir.chmod(0o700)
            except OSError:
                pass
            db_path = db_dir / "state.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()
        try:
            self.db_path.parent.chmod(0o700)
            self.db_path.chmod(0o600)
        except OSError:
            pass
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _ensure_schema(self):
        """Create embeddings table if not exists."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS longterm_embeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL,
                    chunk_start INTEGER NOT NULL,
                    chunk_end INTEGER NOT NULL,
                    embedding BLOB NOT NULL,
                    created_at INTEGER NOT NULL
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_file_path 
                ON longterm_embeddings(file_path)
            """)
            conn.commit()
        finally:
            conn.close()
    
    def store(self, file_path: str, chunk_start: int, chunk_end: int, 
              embedding: bytes) -> int:
        """
        Store an embedding.
        
        Args:
            file_path: Path to the source file
            chunk_start: Start position of chunk in file
            chunk_end: End position of chunk in file
            embedding: Encoded float32 embedding vector
            
        Returns:
            ID of stored embedding
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO longterm_embeddings 
                (file_path, chunk_start, chunk_end, embedding, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (file_path, chunk_start, chunk_end, embedding, int(time.time())))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()
    
    def store_batch(self, embeddings: List[Tuple[str, int, int, bytes]]) -> int:
        """
        Store multiple embeddings in a transaction.
        
        Args:
            embeddings: List of (file_path, chunk_start, chunk_end, embedding)
            
        Returns:
            Number of embeddings stored
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.executemany("""
                INSERT INTO longterm_embeddings 
                (file_path, chunk_start, chunk_end, embedding, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, embeddings)
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()
    
    def search(self, query_embedding: bytes, limit: int = 5) -> List[Dict]:
        """
        Search for similar embeddings using cosine similarity.
        
        Loads all stored embeddings, computes cosine similarity
        against the query vector in Python, and returns the top
        results ranked by similarity score.
        
        Args:
            query_embedding: Encoded float32 query embedding
            limit: Maximum results to return
            
        Returns:
            List of matching embeddings with metadata and similarity score
        """
        try:
            import numpy as np
            query_vec = _decode_embedding(query_embedding)
            if query_vec is None:
                return []
            query_norm = np.linalg.norm(query_vec)
            if query_norm == 0:
                return []
        except Exception as e:
            error(f"Failed to load query embedding: {e}")
            return []

        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, file_path, chunk_start, chunk_end, embedding, created_at
                FROM longterm_embeddings
            """)

            scored = []
            for row in cursor.fetchall():
                try:
                    import numpy as np
                    vec = _decode_embedding(row["embedding"])
                    if vec is None or vec.shape != query_vec.shape:
                        continue
                    vec_norm = np.linalg.norm(vec)
                    if vec_norm == 0:
                        continue
                    score = float(np.dot(query_vec, vec) / (query_norm * vec_norm))
                    scored.append((score, {
                        "id": row["id"],
                        "file_path": row["file_path"],
                        "chunk_start": row["chunk_start"],
                        "chunk_end": row["chunk_end"],
                        "created_at": row["created_at"],
                        "similarity": round(score, 4),
                    }))
                except Exception:
                    continue

            # Sort by similarity descending, return top results
            scored.sort(key=lambda x: x[0], reverse=True)
            return [item for _, item in scored[:limit]]

        finally:
            conn.close()
    
    def get_by_file(self, file_path: str) -> List[Dict]:
        """Get all embeddings for a file."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, file_path, chunk_start, chunk_end, created_at
                FROM longterm_embeddings
                WHERE file_path = ?
                ORDER BY chunk_start
            """, (file_path,))
            
            results = []
            for row in cursor.fetchall():
                results.append(dict(row))
            return results
        finally:
            conn.close()
    
    def delete_by_file(self, file_path: str) -> int:
        """Delete all embeddings for a file."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM longterm_embeddings
                WHERE file_path = ?
            """, (file_path,))
            deleted = cursor.rowcount
            conn.commit()
            return deleted
        finally:
            conn.close()
    
    def count(self) -> int:
        """Get total number of embeddings."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM longterm_embeddings")
            return cursor.fetchone()[0]
        finally:
            conn.close()


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, 
               overlap: int = CHUNK_OVERLAP) -> List[Tuple[str, int, int]]:
    """
    Split text into overlapping chunks.
    
    Args:
        text: Text to chunk
        chunk_size: Maximum characters per chunk
        overlap: Characters of overlap between chunks
        
    Returns:
        List of (chunk_text, start_pos, end_pos) tuples
    """
    chunks = []
    start = 0
    
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end]
        chunks.append((chunk, start, end))
        
        # Move start forward, accounting for overlap
        start = end - overlap
        if start < 0:
            start = end
    
    return chunks


# Global instances
_embedding_model: Optional[EmbeddingModel] = None
_embedding_store: Optional[EmbeddingStore] = None


def get_embedding_model() -> EmbeddingModel:
    """Get the global embedding model instance."""
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = EmbeddingModel()
    return _embedding_model


def get_embedding_store() -> EmbeddingStore:
    """Get the global embedding store instance."""
    global _embedding_store
    if _embedding_store is None:
        _embedding_store = EmbeddingStore()
    return _embedding_store


def reset_embeddings():
    """Reset global instances (for testing)."""
    global _embedding_model, _embedding_store
    _embedding_model = None
    _embedding_store = None
