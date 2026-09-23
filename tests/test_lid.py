"""TASK-003：逐段語言偵測（純函式）。"""
from app import lid


def test_kana_is_ja():
    assert lid.label_cue("こんにちは") == "ja"
    assert lid.label_cue("歌枠リレー") == "ja"


def test_hanzi_is_zh():
    assert lid.label_cue("大家好我是台灣人") == "zh"


def test_latin_is_en():
    assert lid.label_cue("Hello everyone") == "en"


def test_short_inherits_prev():
    assert lid.label_cue("嗯", prev_lang="ja") == "ja"


def test_empty_returns_prev():
    assert lid.label_cue("", prev_lang="zh") == "zh"
