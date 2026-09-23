"""TASK-002：regions 正規化（純函式，無模型、秒級）。"""
from app import regions


def test_empty_means_whole_file():
    assert regions.normalize([], 100.0) == [(0.0, 100.0)]


def test_clamp_and_pad():
    # [10,20] + pad 0.3 → [9.7, 20.3]
    assert regions.normalize([(10, 20)], 100.0, pad=0.3) == [(9.7, 20.3)]


def test_clamp_to_duration():
    out = regions.normalize([(95, 200)], 100.0, pad=0.3)
    assert out[0][1] == 100.0


def test_merge_overlap():
    assert regions.normalize([(0, 10), (9, 20)], 100.0, pad=0.0) == [(0.0, 20.0)]


def test_merge_adjacent_after_pad():
    # 補邊後 [0,10.3] 與 [10.1,20.3] 重疊 → 合併成一段
    out = regions.normalize([(0, 10), (10.4, 20)], 100.0, pad=0.3)
    assert len(out) == 1


def test_drop_tiny_span():
    assert regions.normalize([(10, 10.02)], 100.0, pad=0.0) == []
