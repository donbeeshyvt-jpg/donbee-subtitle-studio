"""TASK-001：reuse 橋接（重用 gen_srt 的 is_tag/fmt）。"""
from app import reuse


def test_is_tag():
    assert reuse.is_tag("[Music]") is True
    assert reuse.is_tag("[唱歌]") is True
    assert reuse.is_tag("hello") is False


def test_fmt():
    assert reuse.fmt(0) == "00:00:00,000"
    assert reuse.fmt(3661.5) == "01:01:01,500"
