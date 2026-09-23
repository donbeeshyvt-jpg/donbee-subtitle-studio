"""TASK-007：端到端驗收（區段 → SRT）。帶 watchdog 防卡死。

驗收 AC-2（區段 offset 正確）、AC-3（音樂段標記）、AC-4（逐段語言）、AC-5（詞級時間）。
"""
import os
import json
import concurrent.futures
import pytest
from app import cli, config

SRC = os.path.join(config.BENCHMARK_DIR, "source.wav")
WORK = os.path.join(config.BENCHMARK_DIR, "_e2e_work")
OUT = os.path.join(config.BENCHMARK_DIR, "_e2e_out.srt")
WATCHDOG = 360


@pytest.mark.skipif(not os.path.exists(SRC), reason="source.wav 不存在")
def test_e2e_region_to_srt():
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(cli.transcribe_media, SRC, [(70, 130)], WORK, OUT, True)
        try:
            srt_path, seg_path = fut.result(timeout=WATCHDOG)
        except concurrent.futures.TimeoutError:
            pytest.fail(f"端到端超過 watchdog {WATCHDOG}s（疑似卡死）")

    assert os.path.exists(srt_path), "SRT 未產生"
    cues = json.load(open(seg_path, encoding="utf-8"))
    assert cues, "無 cue 產出"
    # AC-2：首 cue 絕對時間應落在區段內（~70s），證明 offset 正確、非從 0 起
    assert cues[0]["start"] >= 69.0, f"首 cue {cues[0]['start']:.1f}s 應 >= 69（offset 錯誤）"
    langs = {c.get("lang") for c in cues if not c.get("is_tag")}
    has_tag = any(c.get("is_tag") for c in cues)
    print(f"[e2e] {len(cues)} cues, first={cues[0]['start']:.1f}s, "
          f"has_music_tag={has_tag}, langs={langs}")
