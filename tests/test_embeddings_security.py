"""Embedding persistence must never deserialize Python objects."""

import pickle

import numpy as np

from core.embeddings import EmbeddingStore, _decode_embedding, _encode_embedding


def test_fixed_vector_format_round_trips():
    source = np.array([0.25, -0.5, 1.0], dtype=np.float32)
    encoded = _encode_embedding(source)
    assert encoded.startswith(b"KZV1")
    np.testing.assert_allclose(_decode_embedding(encoded), source)


def test_legacy_pickle_is_rejected_without_deserialization():
    legacy = pickle.dumps(np.array([1.0, 2.0], dtype=np.float32))
    assert _decode_embedding(legacy) is None


def test_search_ignores_legacy_rows(tmp_path):
    store = EmbeddingStore(tmp_path / "state" / "embeddings.db")
    store.store("legacy.py", 0, 1, pickle.dumps(np.array([1.0, 0.0])))
    store.store("safe.py", 0, 1, _encode_embedding([1.0, 0.0]))

    results = store.search(_encode_embedding([1.0, 0.0]))

    assert [result["file_path"] for result in results] == ["safe.py"]
