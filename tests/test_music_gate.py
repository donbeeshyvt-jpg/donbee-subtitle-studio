"""TASK-005：音樂閘（AST）。在歌回基準片上驗證能偵測到唱歌段。

註：首次執行會下載 ~350MB AST 模型。迴圈為有限窗數，無無限迴圈風險。
"""
import os
import pytest
from app import music_gate, config

CLIP = os.path.join(config.BENCHMARK_DIR, "clip120.wav")


@pytest.mark.skipif(not os.path.exists(CLIP), reason="benchmark clip 不存在")
def test_segments_cover_and_find_music():
    spans = music_gate.segment(CLIP, win=1.5)
    assert spans, "未回傳任何區間"
    coverage = spans[-1][2] - spans[0][1]
    assert coverage > 100, f"覆蓋率過低: {coverage:.1f}s"
    labels = {s[0] for s in spans}
    # 歌回片段應至少偵測到一段 music（AC-3 前提）
    assert "music" in labels, f"未偵測到音樂段，labels={labels}"
    print(f"[music_gate] {len(spans)} spans, labels={labels}, coverage={coverage:.1f}s")
