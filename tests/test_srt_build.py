"""TASK-004：SRT 組裝（offset / 合併 / 重編號 / 標籤不被切）。"""
from app import srt_build


def test_offset_segments():
    cues = srt_build.offset_segments([{"start": 1.0, "end": 2.0, "text": "hi"}], 70.0)
    assert cues[0]["start"] == 71.0 and cues[0]["end"] == 72.0


def test_offset_words():
    segs = [{"start": 1.0, "end": 2.0, "text": "hi",
             "words": [{"word": "hi", "start": 1.0, "end": 1.5}]}]
    cues = srt_build.offset_segments(segs, 10.0)
    assert cues[0]["words"][0]["start"] == 11.0


def test_merge_sorts_and_numbers():
    a = [srt_build.make_cue(70, 71, "second")]
    b = [srt_build.make_cue(10, 11, "first")]
    cues = srt_build.merge_and_number([a, b])
    assert [c["text"] for c in cues] == ["first", "second"]


def test_tag_cue_flagged_and_whole():
    c = srt_build.make_cue(0, 30, "[Music]")
    assert c["is_tag"] is True
    s = srt_build.to_srt([c])
    assert "[Music]" in s and "00:00:00,000 --> 00:00:30,000" in s


def test_fix_zero_length():
    out = srt_build.merge_and_number([[srt_build.make_cue(5, 5, "x")]])
    assert out[0]["end"] > out[0]["start"]


def test_show_lang_prefix_skips_tag():
    cues = [srt_build.make_cue(0, 1, "你好", lang="zh"),
            srt_build.make_cue(1, 2, "[Music]")]
    s = srt_build.to_srt(cues, show_lang=True)
    assert "[zh] 你好" in s and "[zh] [Music]" not in s


def test_to_traditional():
    out = srt_build.to_traditional("简体中文测试转换")
    assert out != "简体中文测试转换", "未轉換（opencc 未生效？）"
    assert "簡" in out and "測" in out


def test_regroup_splits_at_sentence_end():
    seg = {"start": 0, "end": 4, "text": "你好世界。再見朋友。",
           "words": [{"word": "你好世界。", "start": 0, "end": 2},
                     {"word": "再見朋友。", "start": 2, "end": 4}]}
    lines = srt_build.regroup_sentences(srt_build.offset_segments([seg], 0), min_chars=3)
    assert [c["text"] for c in lines] == ["你好世界。", "再見朋友。"]
    assert lines[0]["start"] == 0 and lines[1]["end"] == 4


def test_regroup_merges_short_sentences():
    # 預設 min_chars=8：兩個過短句子合併成一條
    seg = {"start": 0, "end": 2, "text": "嗯。好。",
           "words": [{"word": "嗯。", "start": 0, "end": 1}, {"word": "好。", "start": 1, "end": 2}]}
    lines = srt_build.regroup_sentences(srt_build.offset_segments([seg], 0))
    assert len(lines) == 1 and lines[0]["text"] == "嗯。好。"


def test_regroup_keeps_tag():
    out = srt_build.regroup_sentences([srt_build.make_cue(0, 5, "[Music]")])
    assert len(out) == 1 and out[0]["is_tag"]


def test_regroup_oneline_one_sentence_and_strips_punct():
    seg = {"start": 0, "end": 6, "text": "你好，世界。真的嗎？太好了！",
           "words": [{"word": "你好，世界。", "start": 0, "end": 2},
                     {"word": "真的嗎？", "start": 2, "end": 4},
                     {"word": "太好了！", "start": 4, "end": 6}]}
    lines = srt_build.regroup_sentences(srt_build.offset_segments([seg], 0), mode="oneline")
    # 一句一行；去掉 ，。 但保留 ！？
    assert [c["text"] for c in lines] == ["你好世界", "真的嗎？", "太好了！"]
