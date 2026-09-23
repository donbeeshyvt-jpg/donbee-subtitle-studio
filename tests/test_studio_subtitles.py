import copy

import pytest

from app.studio.subtitles import layout_cues, map_cues, to_srt, to_vtt


def cue(text, start=0, end=1000000, **extra):
    return dict(id=f'c{start}', raw_text=text, start_us=start, end_us=end,
                alignment_status='word', **extra)


def test_layout_sentence_count_and_immutable():
    original = [cue(f'第{i}句。', i*1000000, (i+1)*1000000) for i in range(5)]
    before = copy.deepcopy(original)
    assert len(layout_cues(original)) == 5
    combined = layout_cues(original, sentences_per_cue=2)
    assert len(combined) == 3
    assert combined[0]['text'] == '第0句 第1句'
    assert combined[0]['origin_cue_ids'] == ['c0', 'c1000000']
    assert original == before


def test_punctuation_and_protected_tokens():
    source = [cue('今天測試字幕。接著輸出影片！版本 v2.0 值 3.14 WhisperX don\'t foo-bar。')]
    result = layout_cues(source, sentences_per_cue=2)
    text = ' '.join(c['text'] for c in result)
    assert '今天測試字幕 接著輸出影片' in text
    assert all(t in text for t in ['v2.0', '3.14', "don't", 'foo-bar'])
    assert '。' not in text and '！' not in text
    assert '。' in ' '.join(c['text'] for c in layout_cues(source, keep_punctuation=True))


def test_word_boundaries_and_unknown_times():
    words = [dict(id='w1', text='字幕工作室', start_us=0, end_us=500000),
             dict(id='w2', text='WhisperX', start_us=None, end_us=None)]
    original = [cue('字幕工作室 WhisperX', words=words)]
    result = layout_cues(original, max_chars=3)
    assert [c['text'] for c in result] == ['字幕工作室', 'WhisperX']
    assert result[1]['words'][0]['start_us'] is None
    assert 'long_word' in result[1]['warnings']


def test_sentence_split_uses_word_times():
    source = [cue('你好。世界！', end=4000000, words=[
        dict(id='a', text='你好。', start_us=0, end_us=1000000),
        dict(id='b', text='世界！', start_us=2000000, end_us=3000000)])]
    result = layout_cues(source)
    assert [c['text'] for c in result] == ['你好', '世界']
    assert [(c['start_us'], c['end_us']) for c in result] == [(0,1000000),(2000000,3000000)]


def test_gap_prevents_merging_and_layout_validation():
    source = [cue('一',0,1000000),cue('二',4000000,5000000)]
    assert len(layout_cues(source, sentences_per_cue=2)) == 2
    with pytest.raises(ValueError):
        layout_cues(source, sentences_per_cue=3)


def test_reordered_repeated_mapping():
    source = [cue('原詞',612000000,615000000)]
    items = [dict(id='a',kind='clip',start_us=900000000,end_us=920000000),
             dict(id='b',kind='clip',start_us=610000000,end_us=630000000),
             dict(id='c',kind='clip',start_us=610000000,end_us=630000000)]
    mapped = map_cues(source,items)
    assert [c['start_us'] for c in mapped] == [22000000,42000000]
    assert len(set(c['id'] for c in mapped)) == 2
    assert [c['start_us'] for c in map_cues(source,items,timebase='clip')] == [2000000,2000000]
    assert [c['start_us'] for c in map_cues(source,items,timebase='source')] == [612000000,612000000]


def test_cross_boundary_word_midpoint_and_partial():
    source = [cue('甲 乙',0,3000000,words=[
        dict(id='a',text='甲',start_us=0,end_us=1000000),
        dict(id='b',text='乙',start_us=1000000,end_us=3000000)])]
    items = [dict(id='a',kind='clip',start_us=1500000,end_us=2500000)]
    result = map_cues(source,items,require_word=True)
    assert result[0]['text'] == '乙'
    assert result[0]['start_us'] == 0 and result[0]['end_us'] == 1000000
    assert 'truncated_word' in result[0]['warnings']


@pytest.mark.parametrize('status,words', [('stale',[]),('segment',[]),('word',[dict(text='字',start_us=None,end_us=None)])])
def test_require_word_rejects_unknown(status,words):
    source = [cue('字',alignment='unused', words=words)]
    source[0]['alignment_status'] = status
    items = [dict(id='a',kind='clip',start_us=0,end_us=1000000)]
    with pytest.raises(ValueError):
        map_cues(source,items,require_word=True)


def test_partial_segment_warning_and_no_mutation():
    source = [cue('整句',0,2000000)]
    before = copy.deepcopy(source)
    result = map_cues(source,[dict(id='a',kind='clip',start_us=500000,end_us=1500000)])
    assert 'partial_cue' in result[0]['warnings']
    assert source == before


def test_srt_vtt_milliseconds_and_invalid_range():
    source = [cue('字幕',1000500,2000499)]
    assert to_srt(source) == '1\n00:00:01,001 --> 00:00:02,000\n字幕\n'
    assert to_vtt(source).startswith('WEBVTT\n\n00:00:01.001 --> 00:00:02.000')
    with pytest.raises(ValueError):
        to_srt([cue('錯誤',2000000,1000000)])


def test_marker_and_unselected_do_not_add_duration():
    items = [dict(id='m',kind='marker',start_us=0),
             dict(id='x',kind='clip',start_us=0,end_us=1000000,selected=False),
             dict(id='y',kind='clip',start_us=0,end_us=1000000)]
    assert len(map_cues([cue('字')],items)) == 1


def test_chinese_word_join_does_not_insert_spaces_between_characters():
    source = [cue('字幕', words=[dict(text='字',start_us=0,end_us=500000),dict(text='幕',start_us=500000,end_us=1000000)])]
    assert layout_cues(source)[0]['text']=='字幕'


def test_missing_word_sentence_boundaries_are_flagged_coarse():
    result = layout_cues([cue('第一句。第二句！')])
    assert len(result)==2
    assert [c['text'] for c in result]==['第一句','第二句']
    assert all('coarse_sentence_boundary' in c['warnings'] for c in result)


def test_two_sentence_grouping_does_not_count_length_fragments_as_sentences():
    source=[cue('one two. three.',words=[dict(text='one',start_us=0,end_us=200000),dict(text='two.',start_us=200000,end_us=400000),dict(text='three.',start_us=500000,end_us=900000)])]
    result=layout_cues(source,sentences_per_cue=2,max_chars=50)
    assert len(result)==1 and result[0]['text']=='one two three'


# ---- 2026-09-19 M4-B3：逐詞對齊後有只顯示 0.04 秒的字幕（large-v3 與 Breeze-ASR-26 都有），看不到 ----
def test_short_subtitles_are_extended_for_reading_without_overlap_or_leaving_the_clip():
    from app.studio.subtitles import MIN_DISPLAY_US, ensure_min_duration
    assert MIN_DISPLAY_US == 700000
    cues = [dict(id='a', start_us=1_000_000, end_us=1_040_000, text='我在哪兒', sequence_item_id='x'),
            dict(id='b', start_us=1_300_000, end_us=1_340_000, text='好', sequence_item_id='x'),
            dict(id='c', start_us=5_000_000, end_us=5_040_000, text='最後', sequence_item_id='x'),
            dict(id='d', start_us=9_000_000, end_us=9_020_000, text='片段尾', sequence_item_id='y')]
    before = copy.deepcopy(cues)
    result = ensure_min_duration(cues, limits={'x': 5_300_000, 'y': 20_000_000})
    # a：下一則 1.30 秒開始 → 只能延到 1.25（0.25 秒，仍看不清楚）→ 2026-09-20 起併進下一則 b，再延滿 0.7 秒以上；
    # c：片段 x 在 5.30 結束（0.3 秒，可讀下限，不合併）；d：延滿 0.7 秒
    assert [(c['start_us'], c['end_us'], c['text']) for c in result] == [
        (1_000_000, 2_000_000, '我在哪兒 好'), (5_000_000, 5_300_000, '最後'), (9_000_000, 9_700_000, '片段尾')]
    assert cues == before  # 不改傳入資料
    # 已經夠長的不變；下一則緊接著而沒空間延長的 0.02 秒字幕，2026-09-20 起併進下一則（開始時間不提早）
    tight = [dict(id='e', start_us=0, end_us=900_000, text='夠長'), dict(id='f', start_us=900_000, end_us=920_000, text='緊接'),
             dict(id='g', start_us=940_000, end_us=2_000_000, text='下一則')]
    assert [(c['start_us'], c['end_us'], c['text']) for c in ensure_min_duration(tight)] == [(0, 900_000, '夠長'), (900_000, 2_000_000, '緊接 下一則')]
    srt = to_srt(result)
    assert '00:00:01,000 --> 00:00:02,000' in srt and '00:00:09,000 --> 00:00:09,700' in srt


# ---- M4-C1（2026-09-20）字幕分段規則 v2：依停頓切、每則最長 7 秒、每行字數上限與最多兩行 ----
def _w(text, start, end):
    return dict(id=f'w{start}', text=text, normalized_text=text, start_us=start, end_us=end)


def _cue(words, identity='c1'):
    text = ''.join(w['text'] for w in words)
    return dict(id=identity, start_us=words[0]['start_us'], end_us=words[-1]['end_us'], text=text, accepted_text=text,
                words=words, alignment_status='word', sequence_item_id='clip')


def test_a_pause_inside_a_sentence_starts_a_new_subtitle():
    from app.studio.subtitles import PAUSE_SPLIT_US, layout_cues
    assert PAUSE_SPLIT_US == 800000
    words = [_w('我', 0, 300_000), _w('在', 300_000, 600_000), _w('這', 600_000, 900_000),
             _w('你', 2_100_000, 2_400_000), _w('在', 2_400_000, 2_700_000), _w('哪', 2_700_000, 3_000_000)]
    rows = layout_cues([_cue(words)])
    assert [(r['text'], r['start_us'], r['end_us']) for r in rows] == [('我在這', 0, 900_000), ('你在哪', 2_100_000, 3_000_000)]
    # 兩句一則時也不跨停頓合併
    assert len(layout_cues([_cue(words)], sentences_per_cue=2)) == 2


def test_a_long_run_without_pauses_is_cut_at_the_maximum_display_time():
    from app.studio.subtitles import MAX_DISPLAY_US, layout_cues
    assert MAX_DISPLAY_US == 7000000
    words = [_w('字', i * 800_000, (i + 1) * 800_000) for i in range(12)]  # 9.6 秒不停
    rows = layout_cues([_cue(words)])
    assert len(rows) >= 2
    assert all(r['end_us'] - r['start_us'] <= MAX_DISPLAY_US for r in rows)
    assert ''.join(r['text'].replace('\n', '') for r in rows) == '字' * 12  # 不漏字、不切在詞中間


def test_long_text_wraps_to_two_lines_and_then_becomes_another_subtitle():
    from app.studio.subtitles import LINE_UNITS, MAX_LINES, layout_cues, text_width
    assert LINE_UNITS == 36 and MAX_LINES == 2  # 全形算 2 → 每行約 18 個中文字
    words = [_w('中', i * 200_000, (i + 1) * 200_000) for i in range(24)]
    [row] = layout_cues([_cue(words)])
    lines = row['text'].split('\n')
    assert len(lines) == 2 and all(text_width(line) <= LINE_UNITS for line in lines)
    assert ''.join(lines) == '中' * 24
    many = [_w('字', i * 200_000, (i + 1) * 200_000) for i in range(40)]
    rows = layout_cues([_cue(many)])
    assert len(rows) == 2
    assert all(len(r['text'].split('\n')) <= MAX_LINES and all(text_width(x) <= LINE_UNITS for x in r['text'].split('\n')) for r in rows)


def test_sentences_without_word_times_are_kept_whole_but_flagged_when_too_long():
    from app.studio.subtitles import layout_cues
    cue = dict(id='c9', start_us=0, end_us=9_000_000, text='沒有逐詞時間的長句子', accepted_text='沒有逐詞時間的長句子',
               words=[], alignment_status='segment', sequence_item_id='clip')
    [row] = layout_cues([cue])
    assert row['text'] == '沒有逐詞時間的長句子' and 'long_display' in row['warnings']
