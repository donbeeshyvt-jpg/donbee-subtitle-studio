"""Qwen3-ASR 子程序（2026-09-20）：在獨立套件層 `.venv-qwen-asr`（qwen-asr --no-deps）裡執行，借用主環境的 torch 與 transformers 4.57.6。

輸入 JSON：{"model": 本機權重資料夾, "device": "auto"|"cuda"|"cpu", "max_new_tokens": 256, "batch": 16,
            "items": [{"path": wav, "language": "Chinese"|"English"|"Japanese"|null, "context": 提示文字}]}
輸出 JSON：{"ok": true, "results": [{"text", "language"}], "load_sec", "infer_sec", "device"} 或 {"ok": false, "error": {code, message, type}}
一次載入、整批轉錄；主程序不 import torch／qwen_asr，GPU 記憶體隨子程序結束釋放。"""
from __future__ import annotations

import argparse
import json
import sys
import time


def run(spec):
    import torch
    from qwen_asr import Qwen3ASRModel
    wanted = spec.get("device", "auto")
    cuda = wanted != "cpu" and torch.cuda.is_available()
    started = time.monotonic()
    model = Qwen3ASRModel.from_pretrained(spec["model"], dtype=torch.bfloat16 if cuda else torch.float32,
                                          device_map="cuda:0" if cuda else "cpu", max_new_tokens=spec.get("max_new_tokens", 256),
                                          max_inference_batch_size=spec.get("batch", 16))
    loaded = time.monotonic()
    items = spec.get("items", [])
    results = model.transcribe(audio=[item["path"] for item in items], context=[item.get("context") or "" for item in items],
                               language=[item.get("language") for item in items]) if items else []
    done = time.monotonic()
    return dict(ok=True, results=[dict(text=getattr(r, "text", "") or "", language=getattr(r, "language", "") or "") for r in results],
                load_sec=round(loaded - started, 3), infer_sec=round(done - loaded, 3), device="cuda" if cuda else "cpu")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    with open(args.input, encoding="utf-8") as stream:
        spec = json.load(stream)
    try:
        outcome = run(spec)
    except Exception as error:  # 子程序內任何失敗都寫成穩定錯誤，交給主程序決定保留草稿
        text = str(error)
        code = "GPU_MEMORY_INSUFFICIENT" if "out of memory" in text.lower() else "QWEN_ASR_FAILED"
        outcome = dict(ok=False, error=dict(code=code, message=text[:500], type=type(error).__name__))
    with open(args.output, "w", encoding="utf-8") as stream:
        json.dump(outcome, stream, ensure_ascii=False)
    return 0 if outcome.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
