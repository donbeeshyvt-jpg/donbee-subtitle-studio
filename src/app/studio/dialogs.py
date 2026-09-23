"""本機原生「選擇資料夾／檔案」視窗：服務跑在使用者自己的電腦上，由後端開視窗把絕對路徑交給網頁（瀏覽器拿不到本機路徑）。
用標準函式庫 tkinter，另開子程序執行（GUI 主迴圈不能在 API 執行緒裡跑）；沒有桌面或沒有 tkinter 時回 DialogUnavailable，介面退回手動輸入。"""
from __future__ import annotations

import json
import subprocess
import sys

MEDIA_PATTERNS = "*.mp4 *.mkv *.webm *.mov *.wav *.mp3 *.m4a *.aac *.flac *.ogg"

_SCRIPT = r"""
import json, sys
kind, title, initial, patterns = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
import tkinter
from tkinter import filedialog
root = tkinter.Tk()
root.withdraw()
root.attributes('-topmost', True)
options = {'title': title or None, 'parent': root}
if initial:
    options['initialdir'] = initial
if kind == 'folder':
    value = filedialog.askdirectory(mustexist=False, **options)
else:
    value = filedialog.askopenfilename(filetypes=[('影音檔', patterns), ('所有檔案', '*.*')], **options)
root.destroy()
print(json.dumps({'path': value or None}, ensure_ascii=False))
"""


class DialogUnavailable(RuntimeError):
    """這台機器開不了原生視窗（沒有桌面工作階段或沒有 tkinter）。"""


def native_dialog(kind, title=None, initial=None, timeout=600):
    """kind＝folder／file；回傳絕對路徑字串，使用者按取消回 None。"""
    if kind not in ("folder", "file"):
        raise ValueError("kind 必須是 folder 或 file")
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _SCRIPT, kind, title or "", initial or "", MEDIA_PATTERNS],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DialogUnavailable(str(error)) from None
    if completed.returncode != 0:
        raise DialogUnavailable((completed.stderr or "").strip()[-300:] or "dialog failed")
    try:
        value = json.loads(completed.stdout.strip().splitlines()[-1]).get("path")
    except (ValueError, IndexError):
        raise DialogUnavailable("dialog returned no result") from None
    return value or None
