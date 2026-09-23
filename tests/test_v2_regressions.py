"""P0：模型身份、未知時間與不可變詞資料的缺陷回歸。"""
from copy import deepcopy
from types import SimpleNamespace
import sys

from app import asr_default, srt_build


def test_missing_end_has_single_offset_and_unknown_word_times_remain_null():
    source = [{"start": 1, "end": None, "text": "hello", "words": [
        {"word": "hello", "start": 0, "end": None, "score": 0.9},
        {"word": "unknown"},
    ]}]
    original = deepcopy(source)
    cue = srt_build.offset_segments(source, 70)[0]
    assert (cue["start"], cue["end"]) == (71, 71)
    assert cue["words"][0]["start"] == 70
    assert cue["words"][0]["end"] is None
    assert cue["words"][1]["start"] is None
    assert source == original


def test_regroup_preserves_raw_words_without_mutating_input():
    cues = [srt_build.make_cue(0, 2, "Hello world!", "en", [
        {"word": "Hello ", "start": 0, "end": 1, "score": 0.9},
        {"word": "world!", "start": 1, "end": 2, "score": 0.8},
    ])]
    original = deepcopy(cues)
    result = srt_build.regroup_sentences(cues, mode="oneline")
    assert result[0]["words"] == original[0]["words"]
    assert result[0]["lang"] == "en"
    assert cues == original
    result[0]["words"][0]["word"] = "changed"
    assert cues == original


def test_model_identity_includes_model_device_compute(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "whisperx", SimpleNamespace(
        load_model=lambda name, **kw: calls.append((name, kw)) or object()))
    monkeypatch.setattr(asr_default, "_model", None)
    monkeypatch.setattr(asr_default, "_device", lambda: "cpu")
    first = asr_default._load_model("tiny", "int8")
    second = asr_default._load_model("large-v3", "int8")
    assert first is not second
    assert second is asr_default._load_model("large-v3", "int8")
    assert second is not asr_default._load_model("large-v3", "float32")
    assert len(calls) == 3


def test_alignment_cache_is_device_specific(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "whisperx", SimpleNamespace(
        load_align_model=lambda **kw: calls.append(kw) or (object(), {})))
    monkeypatch.setattr(asr_default, "_align_cache", {})
    cpu = asr_default._get_align("zh", "cpu")
    assert cpu is not asr_default._get_align("zh", "cuda")
    assert cpu is asr_default._get_align("zh", "cpu")
