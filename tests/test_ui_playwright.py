"""TASK-104：模擬使用者操作 UI（Playwright headless）。

涵蓋：分頁切換 → 載入本機檔 → wavesurfer 就緒 → 建立兩區段 → 單獨刪除一個(✕) →
引擎↔模型連動 → 產生字幕。/transcribe 以路由攔截回假結果。全程 timeout，無無限迴圈。
截圖存 benchmark/ui_screenshot.png 供人工檢視（ui-ux-pro-max）。
"""
import os
import json
import time
import socket
import threading
import pytest
from app import config

CLIP = os.path.join(config.BENCHMARK_DIR, "clip120.wav")
SHOT = os.path.join(config.BENCHMARK_DIR, "ui_screenshot.png")


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    return port


@pytest.fixture(scope="module")
def server_url():
    import uvicorn
    from app.server import app
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close(); break
        except OSError:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True


@pytest.mark.skipif(not os.path.exists(CLIP), reason="clip120.wav 不存在")
def test_user_flow(server_url):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        page.route("**/transcribe", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"srt": "C:/fake/out.srt", "segments": "C:/fake/out.segments.json"})))

        page.goto(server_url, wait_until="domcontentloaded")

        # 1) 初始在「下載」分頁；編輯分頁隱藏
        assert page.is_hidden("#tab-edit"), "編輯分頁初始應隱藏"
        assert page.is_visible("#tab-download"), "下載分頁初始應顯示"

        # 2) 切到編輯分頁 → 用檔案選擇器選檔（模擬使用者選檔→上傳→載入）
        page.click(".tab[data-tab='edit']")
        page.wait_for_selector("#fileInput", state="attached", timeout=5000)
        page.set_input_files("#fileInput", CLIP)
        page.wait_for_function("() => window.__wsReady === true", timeout=40000)

        # 3) 建立兩區段
        page.evaluate("() => { window.__app.addRegion(5,15); window.__app.addRegion(40,55); }")
        page.wait_for_function(
            "() => document.querySelectorAll('#regionList li').length === 2", timeout=5000)

        # 4) 單獨刪除一個區段（清單上的 ✕）→ 剩 1
        page.click("#regionList li:first-child .btn-x")
        page.wait_for_function(
            "() => document.querySelectorAll('#regionList li').length === 1", timeout=5000)

        # 5) 引擎↔模型連動：選 hybrid → 模型選擇隱藏；選回 whisperx → 顯示
        page.select_option("#engine", "hybrid")
        assert page.evaluate("() => getComputedStyle(document.querySelector('#modelWrap')).display") == "none"
        page.select_option("#engine", "whisperx")
        assert page.evaluate("() => getComputedStyle(document.querySelector('#modelWrap')).display") != "none"

        # 6) 產生字幕
        page.click("#genBtn")
        page.wait_for_function(
            "() => document.querySelector('#status').textContent.includes('完成')", timeout=10000)

        page.screenshot(path=SHOT, full_page=True)
        status = page.text_content("#status")
        regions = page.evaluate("() => window.__app.getRegions()")
        browser.close()

    assert "完成" in status, f"狀態未顯示完成：{status}"
    assert len(regions) == 1, f"刪除後區段數應為 1：{regions}"
    print(f"[ui] regions={regions}, status='{status}', screenshot={SHOT}")
