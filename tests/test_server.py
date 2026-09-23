"""TASK-101：FastAPI 後端 HTTP 層。重型管線以 monkeypatch 取代，秒級、無模型、無迴圈。"""
import os
import pytest
from fastapi.testclient import TestClient
from app import server, cli, config

SRC = os.path.join(config.BENCHMARK_DIR, "source.wav")
CLIP = os.path.join(config.BENCHMARK_DIR, "clip120.wav")
client = TestClient(server.app)


def test_index_served():
    r = client.get("/")
    assert r.status_code == 200 and "字幕工作室" in r.text


def test_open_and_media_range():
    if not os.path.exists(SRC):
        pytest.skip("source.wav 不存在")
    info = client.post("/open", json={"path": SRC}).json()
    assert info["id"] and info["duration"] and info["duration"] > 0
    rr = client.get(f"/media/{info['id']}", headers={"Range": "bytes=0-1023"})
    assert rr.status_code == 206
    assert "content-range" in {k.lower() for k in rr.headers}


def test_open_missing_file():
    missing = os.path.join(config.BENCHMARK_DIR, "nope", "nope.mp4")
    assert client.post("/open", json={"path": missing}).status_code == 404


def test_transcribe_routes_to_pipeline(monkeypatch):
    if not os.path.exists(SRC):
        pytest.skip("source.wav 不存在")
    monkeypatch.setattr(cli, "transcribe_media",
                        lambda *a, **k: ("out.srt", "out.segments.json"))
    rid = client.post("/open", json={"path": SRC}).json()["id"]
    r = client.post("/transcribe", json={"id": rid, "regions": [[70, 130]], "show_lang": True})
    assert r.status_code == 200 and r.json()["srt"] == "out.srt"


def test_transcribe_bad_id():
    assert client.post("/transcribe", json={"id": "deadbeef", "regions": []}).status_code == 404


def test_download_job_flow(monkeypatch):
    """新版：/download 啟背景 job，輪詢 /download_status 直到 done。"""
    if not os.path.exists(SRC):
        pytest.skip("source.wav 不存在")
    import time
    from app import download
    monkeypatch.setattr(download, "fetch",
                        lambda url, out_dir, **k: {"path": SRC, "title": "t", "duration": 5.0})
    job = client.post("/download", json={"url": "https://youtu.be/xxxx"}).json()["job"]
    st = {}
    for _ in range(50):                      # 最多等 5s（有上限，不無限等）
        st = client.get(f"/download_status/{job}").json()
        if st["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert st.get("status") == "done", st
    assert st["id"] and st["duration"]


def test_upload(monkeypatch):
    """使用者選檔上傳 → 註冊 + 回 metadata。"""
    if not os.path.exists(CLIP):
        pytest.skip("clip120.wav 不存在")
    with open(CLIP, "rb") as f:
        r = client.post("/upload", files={"file": ("clip120.wav", f, "audio/wav")})
    j = r.json()
    assert r.status_code == 200 and j["id"] and j["duration"] == 120.0
