"""Regression tests for Kuza 2.1 hardening."""
import pickle
from pathlib import Path
from core.action_policy import ActionRequest, authorize
from core.embeddings import EmbeddingStore, _decode_embedding, _encode_embedding
from core.state import StateStore

def test_pending_cancel_is_terminal(tmp_path):
    state = StateStore(tmp_path / "state.db")
    task_id = state.add_task("do work")
    assert state.cancel_task(task_id) is True
    assert state.get_task(task_id)["status"] == "cancelled"
    assert state.get_next_pending() is None
    state.complete_task(task_id, "late completion")
    assert state.get_task(task_id)["status"] == "cancelled"
    state.close()

def test_running_cancel_cannot_be_overwritten(tmp_path):
    state = StateStore(tmp_path / "state.db")
    task_id = state.add_task("do work")
    assert state.try_claim_task(task_id) is True
    assert state.cancel_task(task_id) is True
    state.fail_task(task_id, "late failure")
    state.complete_task(task_id, "late completion")
    assert state.get_task(task_id)["status"] == "cancelled"
    assert state.is_task_cancelled(task_id) is True
    state.close()

def test_action_policy_cancellation_wins(monkeypatch):
    from utils.config import AGENT_CONFIG
    monkeypatch.setitem(AGENT_CONFIG, "_cancel_check", lambda: True)
    allowed, reason = authorize(ActionRequest("write", "write a file", "x.py"), yolo=True)
    assert allowed is False
    assert "cancel" in reason.lower()

def test_embedding_format_rejects_pickle():
    assert _decode_embedding(pickle.dumps([1.0, 2.0])) is None

def test_embedding_store_batch_uses_timestamp(tmp_path):
    store = EmbeddingStore(tmp_path / "vectors.db")
    vector = _encode_embedding([1.0, 0.0, 0.0])
    assert store.store_batch([("a.py", 0, 3, vector)]) == 1
    assert store.count() == 1
    assert store.search(vector, limit=1)[0]["file_path"] == "a.py"

def test_real_kuza_launcher_exists():
    root = Path(__file__).resolve().parents[1]
    assert (root / "kuza").is_file()

def test_gui_default_is_loopback():
    root = Path(__file__).resolve().parents[1]
    source = (root / "gui" / "server.py").read_text(encoding="utf-8")
    assert "KUZA_GUI_HOST', '127.0.0.1'" in source
    assert "KUZA_GUI_TOKEN" in source

def test_checkpoint_covers_operational_files():
    from core.checkpoint import is_core_file
    assert is_core_file("kuzad2")
    assert is_core_file("install.sh")
    assert is_core_file("gui/server.py")
    assert is_core_file(".github/workflows/ci.yml")
