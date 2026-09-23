"""TASK-103/feature 003：混合引擎（VibeVoice 子行程 + whisperx 對齊）。

帶 watchdog：asr_hybrid 內部對子行程有超時殺除；本測試外層再加總超時，
確保不會無限等待（呼應使用者「避免無限迴圈」+ 子行程隔離設計）。
"""
import os
import concurrent.futures
import pytest
from app import asr_hybrid, config

CLIP = os.path.join(config.BENCHMARK_DIR, "hybrid_clip.wav")
WATCHDOG = 300


@pytest.mark.skipif(not os.path.exists(CLIP), reason="hybrid_clip.wav 不存在")
def test_hybrid_produces_segments():
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(asr_hybrid.transcribe_align, CLIP)
        try:
            res = fut.result(timeout=WATCHDOG)
        except concurrent.futures.TimeoutError:
            pytest.fail(f"hybrid 超過 watchdog {WATCHDOG}s")
    assert res["segments"], "hybrid 未產出 segments（且未成功退回 WhisperX）"
    # 至少要有文字
    assert any((s.get("text") or "").strip() for s in res["segments"])
    print(f"[hybrid] lang={res['language']}, {len(res['segments'])} segs, "
          f"first='{res['segments'][0].get('text','')[:36]}', "
          f"has_words={any(s.get('words') for s in res['segments'])}")
