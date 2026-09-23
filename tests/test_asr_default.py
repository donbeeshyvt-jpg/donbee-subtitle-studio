"""TASK-006：WhisperX 轉錄+對齊。帶 watchdog（防卡死），驗證產出含 words 的 segments。"""
import os
import concurrent.futures
import pytest
from app import asr_default, config

CLIP = os.path.join(config.BENCHMARK_DIR, "clip120.wav")
WATCHDOG_SEC = 240          # 超時即視為失敗（不讓測試無限等待）


@pytest.mark.skipif(not os.path.exists(CLIP), reason="benchmark clip 不存在")
def test_transcribe_align_has_words():
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(asr_default.transcribe_align, CLIP)
        try:
            result = fut.result(timeout=WATCHDOG_SEC)
        except concurrent.futures.TimeoutError:
            pytest.fail(f"ASR 超過 watchdog {WATCHDOG_SEC}s（疑似卡死）")
    segs = result["segments"]
    assert segs, "未產出 segments"
    assert result["language"] in ("ja", "zh", "en"), f"語言={result['language']}"
    # 至少一段有逐字對齊（words）
    assert any(s.get("words") for s in segs), "無逐字對齊 words"
    print(f"[asr_default] lang={result['language']}, {len(segs)} segs, "
          f"first='{segs[0].get('text','')[:30]}'")


def test_breeze25_is_the_default_and_heads_the_list_with_large_v3_second(tmp_path):
    """2026-09-20 使用者：「預設 asr25 為第一個，再來次要是 v3」。25 沒安裝時仍退回 large-v3。"""
    from app.studio import asr_models
    assert asr_models.REFINE_DEFAULT == 'breeze-asr-25'
    keys = [item['key'] for item in asr_models.catalog(models_dir=tmp_path)]
    assert keys[:2] == ['breeze-asr-25', 'large-v3']  # 清單順序：25 第一、v3 第二
    assert asr_models.default_model(models_dir=tmp_path) == 'large-v3'  # 25 還沒安裝就退回 v3
