"""刪除專案（2026-09-20 使用者：「工作室的專案整理一下，保留最近的比對專案」）。

不可復原，所以規則講死：只刪這個專案自己的資料與成果檔；還在跑的工作先擋下；
使用者的原始影音與輸出資料夾（資料目錄以外）一律不碰。
"""
import json

import pytest
from fastapi.testclient import TestClient

from app.studio.api import create_app
from app.studio.store import Store, StudioError


def seeded(tmp_path):
    store = Store(tmp_path / "state")
    keep = store.create("project", {"name": "要留的"})["id"]
    drop = store.create("project", {"name": "要刪的"})["id"]
    files = {}
    for project in (keep, drop):
        job = store.submit(project, {"kind": "probe", "source_id": "s"}, "cpu")
        store.claim("cpu")
        store.create("source", {"name": "素材", "path": str(tmp_path / "原始影片.mp4")}, project)
        store.set_head(f"sequence:{project}", store.create("sequence", {"items": []}, project)["id"]) \
            if hasattr(store, "set_head") else None
        artifact = store.publish(project, job["id"], "subtitle", f"{project} 的字幕".encode("utf-8"), "srt")
        store.finish(job["id"], "succeeded", result={"artifact_id": artifact["id"]})
        files[project] = store.artifact_path(artifact["id"])
    original = tmp_path / "原始影片.mp4"
    original.write_bytes(b"MP4")
    return store, keep, drop, files, original


def test_deleting_a_project_removes_only_its_own_rows_and_files(tmp_path):
    store, keep, drop, files, original = seeded(tmp_path)
    report = store.delete_project(drop)
    assert report["project_id"] == drop and report["removed_artifacts"] == 1 and report["removed_jobs"] == 1
    assert report["freed_bytes"] > 0 and report["removed_entities"] >= 2
    assert not files[drop].exists()  # 成果檔一起刪
    assert files[keep].exists() and original.read_bytes() == b"MP4"  # 別的專案與原始素材不動
    with pytest.raises(StudioError) as missing:
        store.get(drop, "project")
    assert missing.value.code == "NOT_FOUND"
    assert store.get(keep, "project")["name"] == "要留的"
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs WHERE project_id=?", (drop,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM entities WHERE project_id=?", (drop,)).fetchone()[0] == 0


def test_a_project_with_work_in_flight_is_not_deleted(tmp_path):
    store, keep, drop, files, _ = seeded(tmp_path)
    store.submit(drop, {"kind": "probe", "source_id": "s2"}, "cpu")  # 排隊中
    with pytest.raises(StudioError) as busy:
        store.delete_project(drop)
    assert busy.value.code == "PROJECT_BUSY" and busy.value.status == 409
    assert store.get(drop, "project")["name"] == "要刪的"  # 什麼都沒刪
    assert files[drop].exists()


def test_the_api_and_cli_delete_the_same_way(tmp_path):
    store, keep, drop, files, _ = seeded(tmp_path)
    app = create_app(tmp_path / "state", start_workers=False)
    client = TestClient(app)
    assert client.get("/v1/session").status_code == 200
    response = client.delete(f"/v1/projects/{drop}")
    assert response.status_code == 200 and response.json()["removed_artifacts"] == 1
    assert client.get(f"/v1/projects/{drop}").status_code == 404
    assert client.delete(f"/v1/projects/{drop}").status_code == 404  # 刪過就沒了
    assert client.get(f"/v1/projects/{keep}").status_code == 200


def test_cli_delete_needs_the_confirm_flag(capsys, monkeypatch):
    import httpx
    from app.studio.cli import main
    monkeypatch.setenv("STUDIO_API_TOKEN", "test-token")
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"project_id": "p1", "removed_artifacts": 2, "removed_jobs": 3,
                                         "removed_entities": 9, "freed_bytes": 1234})

    with httpx.Client(transport=httpx.MockTransport(handler)) as api:
        assert main(["project", "delete", "p1", "--json"], client=api) == 2  # 沒加 --yes 不刪
        assert not seen
        assert main(["project", "delete", "p1", "--yes", "--json"], client=api) == 0
    assert seen[0].method == "DELETE" and str(seen[0].url).endswith("/v1/projects/p1")
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["freed_bytes"] == 1234
