"""啟動器：背景起 uvicorn，前景開 pywebview 視窗（WebView2）。

穩健設計：
- 一定印出本機網址（即使視窗沒開，使用者也能自己用瀏覽器開）。
- pywebview 開窗失敗 → 自動退回系統瀏覽器。
- `--browser` 直接用系統瀏覽器（最可靠，繞過 WebView2 相容問題）。

選檔：pywebview 視窗提供原生檔案對話框(Api.pick_file)，回傳「真實路徑」→ 前端 /open
直接讀檔，零複製、秒載入。瀏覽器模式無法取得路徑，才退回上傳。
"""
import sys
import time
import threading
import uvicorn

_MEDIA_TYPES = ("媒體檔案 (*.mp4;*.mkv;*.mov;*.webm;*.avi;*.wav;*.mp3;*.m4a;*.flac;*.aac;*.ogg)",
                "所有檔案 (*.*)")


class Api:
    """暴露給前端 JS 的橋接（window.pywebview.api.*）。"""

    def pick_file(self):
        """開原生檔案對話框，回傳選到的絕對路徑（取消則 None）。"""
        import webview
        res = webview.windows[0].create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False, file_types=_MEDIA_TYPES)
        if not res:
            return None
        return res[0] if isinstance(res, (list, tuple)) else res


def _run_server(host, port):
    from app.server import app
    uvicorn.run(app, host=host, port=port, log_level="warning")


def _serve_forever():
    print("伺服器執行中，關閉此視窗即結束。")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def main(host="127.0.0.1", port=8765, browser=False):
    url = f"http://{host}:{port}/"
    threading.Thread(target=_run_server, args=(host, port), daemon=True).start()
    time.sleep(1.5)
    print(f"\n=== 字幕工作室已啟動： {url} ===")
    print("（若沒有視窗跳出，請直接用瀏覽器開上面這個網址）\n")

    if browser:
        import webbrowser
        webbrowser.open(url)
        _serve_forever()
        return

    try:
        import webview
        webview.create_window("字幕工作室 Subtitle Studio", url,
                              width=1200, height=820, js_api=Api())
        webview.start()
    except Exception as e:   # noqa: BLE001
        print(f"[啟動器] 內建視窗開啟失敗（{e}）→ 改用系統瀏覽器")
        import webbrowser
        webbrowser.open(url)
        _serve_forever()


if __name__ == "__main__":
    main(browser="--browser" in sys.argv)
