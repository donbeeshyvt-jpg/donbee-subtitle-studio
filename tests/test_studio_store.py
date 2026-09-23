"""持久版本、冪等、終態競爭與發布完整性。"""
import pytest
from app.studio.store import Store, StudioError


def test_revision_survives_restart_and_conflicts(tmp_path):
    store = Store(tmp_path)
    project = store.create("project", {"name": "test"})["id"]
    first = store.revise(project, "sequence", {"items": [3, 1, 2]}, "seq_00", project)
    second = Store(tmp_path)
    assert second.get(first["id"])["items"] == [3, 1, 2]
    with pytest.raises(StudioError, match="資料已更新"):
        second.revise(project, "sequence", {"items": []}, "seq_00", project)


def test_idempotency_and_claim(tmp_path):
    store = Store(tmp_path)
    project = store.create("project", {"name": "test"})["id"]
    body = {"kind": "probe"}
    job = store.submit(project, body, key="once")
    assert store.submit(project, body, key="once")["id"] == job["id"]
    with pytest.raises(StudioError) as exc:
        store.submit(project, {"kind": "acquire"}, key="once")
    assert exc.value.code == "IDEMPOTENCY_CONFLICT"
    assert store.claim("cpu")["id"] == job["id"]
    assert store.claim("cpu") is None


def test_cancel_wins_before_publication(tmp_path):
    store = Store(tmp_path)
    project = store.create("project", {"name": "test"})["id"]
    job = store.submit(project, {"kind": "probe"})["id"]
    store.claim("cpu")
    store.cancel(job)
    store.finish(job, "succeeded", {"should_not_be_returned": True})
    assert store.job(job)["status"] == "cancelled"
    assert store.job(job)["result"] is None
    assert not store.finish(job, "failed")


def test_pause_priority_and_events(tmp_path):
    store = Store(tmp_path)
    project = store.create("project", {"name": "test"})["id"]
    first = store.submit(project, {"kind": "probe"})["id"]
    second = store.submit(project, {"kind": "probe"})["id"]
    store.control(first, priority=100, dispatch_paused=True)
    assert store.claim("cpu")["id"] == second
    store.control(first, dispatch_paused=False)
    assert store.claim("cpu")["id"] == first
    events = store.events(first)
    assert store.events(first, events[0]["id"])[0]["id"] > events[0]["id"]


def test_atomic_artifact_integrity(tmp_path):
    store = Store(tmp_path)
    project = store.create("project", {"name": "test"})["id"]
    job = store.submit(project, {"kind": "export"})["id"]
    artifact = store.publish(project, job, "srt", b"subtitle", "srt")
    path = store.artifact_path(artifact["id"])
    assert path.read_bytes() == b"subtitle"
    path.write_bytes(b"broken")
    with pytest.raises(StudioError) as exc:
        store.artifact_path(artifact["id"])
    assert exc.value.code == "ARTIFACT_CORRUPT"
