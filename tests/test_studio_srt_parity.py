"""2026-09-20 使用者第 6 點：「請確實對準 SRT 時間軸，SRT 檔案多少就是多少，不要預覽不同步；運算字幕時就要校正好。」
- 精修（有時間戳的模型）只收目標句範圍內的字：前後補的 0.3 秒上下文裡的字不收，避免相鄰字幕邊界重複（R14 CP 比對：Breeze 3 處、large-v3 1 處）。
- 草稿第一趟跳過的長段落（R14 CP 比對：Breeze 專案的 turbo 草稿 29.6～40.8 秒整段沒有字）要補轉。
- 延長後仍短到看不清楚（< 0.3 秒）的字幕，併進相鄰字幕（開始時間不提早）。
- 網頁預覽用與匯出 SRT 同一段程式計算，內容逐字相同。"""
import hashlib
import sys
from types import SimpleNamespace

import numpy as np
from fastapi.testclient import TestClient

from app.studio import asr, subtitles
from app.studio.store import Store
from app.studio.worker import Worker


def seg(start, end, text, words=(), **extra):
    return SimpleNamespace(text=text, start=start, end=end, avg_logprob=extra.get('logprob', -.3), compression_ratio=1.2,
                           no_speech_prob=extra.get('no_speech', .05),
                           words=[SimpleNamespace(word=w, start=s, end=e, probability=.9) for w, s, e in words])


def test_refine_keeps_only_the_words_inside_the_target_sentence(monkeypatch):
    # 目標句 1.0～2.0 秒；精修時前後各多送 0.3 秒。模型把上下文裡的「然後」「好」也寫進來 → 不收
    def make_model(model, **kwargs):
        def transcribe(audio, **options):
            words = [('然後', 0.0, 0.25), ('一個人', 0.35, 0.9), ('在上面', 0.95, 1.25), ('好', 1.35, 1.55)]
            return iter([seg(0.0, 1.6, '然後一個人在上面好', words)]), SimpleNamespace(language='zh', duration=1.6, duration_after_vad=1.6)
        return SimpleNamespace(transcribe=transcribe)
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=make_model))
    monkeypatch.setattr(asr, '_load_audio', lambda path: np.ones(16000 * 4, dtype=np.float32) * .1)
    # 前一句結尾正是「然後」、下一句開頭正是「好」：上下文裡的這兩個字是鄰句的重複 → 不收
    before = dict(id='p', start_us=0, end_us=950_000, raw_text='我們然後', accepted_text='我們然後', lang='zh', alignment_status='segment', words=[])
    cue = dict(id='a', start_us=1_000_000, end_us=2_000_000, raw_text='一個人在上面', accepted_text='一個人在上面', lang='zh', alignment_status='segment', words=[])
    after = dict(id='n', start_us=2_050_000, end_us=3_000_000, raw_text='好的', accepted_text='好的', lang='zh', alignment_status='segment', words=[])
    result = asr.refine([before, cue, after], 'audio.wav', device='cpu', cue_ids=['a'], max_refine_audio_ratio=1.0, context_us=300_000, language_policy='zh')
    row = next(c for c in result['cues'] if c.get('origin_cue_ids') == ['a'])
    assert row['accepted_text'] == '一個人在上面' and [w['text'] for w in row['words']] == ['一個人', '在上面']
    assert 1_000_000 <= row['start_us'] and row['end_us'] <= 2_000_000
    assert row['refinement_provenance']['context_words_trimmed'] == 2


def test_refine_keeps_edge_words_that_are_not_repeated_by_the_neighbours(monkeypatch):
    # 2026-09-20 同一草稿比對：草稿句界不準，只看時間會把「確實」切成「實」、「紅毛」切成「毛」；鄰句沒有這些字就是本句的字，要留
    def make_model(model, **kwargs):
        def transcribe(audio, **options):
            words = [('確', 0.1, 0.28), ('實', 0.3, 0.6), ('在上面', 0.65, 1.2)]
            return iter([seg(0.0, 1.3, '確實在上面', words)]), SimpleNamespace(language='zh', duration=1.6, duration_after_vad=1.6)
        return SimpleNamespace(transcribe=transcribe)
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=make_model))
    monkeypatch.setattr(asr, '_load_audio', lambda path: np.ones(16000 * 4, dtype=np.float32) * .1)
    before = dict(id='p', start_us=0, end_us=900_000, raw_text='我們走吧', accepted_text='我們走吧', lang='zh', alignment_status='segment', words=[])
    cue = dict(id='a', start_us=1_000_000, end_us=2_000_000, raw_text='實在上面', accepted_text='實在上面', lang='zh', alignment_status='segment', words=[])
    result = asr.refine([before, cue], 'audio.wav', device='cpu', cue_ids=['a'], max_refine_audio_ratio=1.0, context_us=300_000, language_policy='zh')
    row = next(c for c in result['cues'] if c.get('origin_cue_ids') == ['a'])
    assert row['accepted_text'] == '確實在上面' and 'context_words_trimmed' not in row['refinement_provenance']


def test_draft_transcribes_a_long_stretch_the_first_pass_skipped(monkeypatch):
    calls = []

    def make_model(model, **kwargs):
        def transcribe(audio, **options):
            calls.append((len(audio) / 16000, options.get('vad_filter')))
            if len(audio) == 20 * 16000:  # 第一趟：2～15 秒整段跳過
                return iter([seg(0.5, 2.0, '開頭'), seg(15.0, 17.0, '結尾')]), SimpleNamespace(language='zh', duration=20.0, duration_after_vad=20.0)
            return iter([seg(1.0, 3.0, '中間這段'), seg(5.0, 6.0, '字幕by某某', no_speech=.9)]), SimpleNamespace(language='zh', duration=len(audio) / 16000, duration_after_vad=len(audio) / 16000)
        return SimpleNamespace(transcribe=transcribe)
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=make_model))
    audio = np.ones(20 * 16000, dtype=np.float32) * .1
    audio[17 * 16000:] = 0  # 17～20 秒真的沒聲音：不補轉
    result = asr.draft(audio, model='turbo', device='cpu', language_policy='zh')
    texts = [(round(c['start_us'] / 1e6, 2), c['accepted_text']) for c in result['cues']]
    assert texts == [(0.5, '開頭'), (3.0, '中間這段'), (15.0, '結尾')], texts
    filled = result['cues'][1]
    assert 'draft_gap_filled' in filled['review_flags']
    assert result['settings']['gap_fill'] == {'gaps_checked': 1, 'cues_added': 1, 'min_gap_sec': 3.0}
    assert [round(seconds, 1) for seconds, _ in calls] == [20.0, 13.0]  # 只補轉有聲音的那段


def test_unreadably_short_subtitle_merges_into_the_next_one_without_starting_early():
    cues = [dict(id='a', start_us=1_000_000, end_us=1_030_000, text='啊', words=[], origin_cue_ids=['a'], warnings=[], sequence_item_id='clip'),
            dict(id='b', start_us=1_030_000, end_us=2_500_000, text='救我', words=[], origin_cue_ids=['b'], warnings=[], sequence_item_id='clip'),
            dict(id='c', start_us=5_000_000, end_us=5_020_000, text='好', words=[], origin_cue_ids=['c'], warnings=[], sequence_item_id='clip')]
    fixed = subtitles.ensure_min_duration(cues, {'clip': 9_000_000})
    assert [(c['start_us'], c['end_us'], c['text']) for c in fixed] == [(1_000_000, 2_500_000, '啊 救我'), (5_000_000, 5_700_000, '好')]
    assert fixed[0]['origin_cue_ids'] == ['a', 'b'] and 'merged_short_cue' in fixed[0]['warnings']
    assert cues[0]['end_us'] == 1_030_000  # 不改傳入資料


def build_project(root):
    path = root / 'audio.wav'
    path.write_bytes(b'RIFF' + b'\0' * 64)
    store = Store(root / 'state')
    p = store.create('project', {'name': '預覽＝SRT'})['id']
    source = store.create('source', {'kind': 'local', 'path': str(path), 'title': 'audio.wav'}, p)['id']
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    asset = store.create('asset', {'source_id': source, 'kind': 'audio', 'path': str(path), 'map_revision': 'map', 'content_hash': digest,
        'source_map': [{'id': 'map', 'source_start_us': 0, 'source_end_us': 3000000, 'asset_start_us': 0, 'asset_end_us': 3000000, 'status': 'verified'}]}, p)
    cues = [{'id': 'cue', 'start_us': 240000, 'end_us': 1380000, 'raw_text': '真的!是我!', 'accepted_text': '真的!是我!', 'lang': 'zh', 'words': [], 'alignment_status': 'segment'},
            {'id': 'short', 'start_us': 1_400_000, 'end_us': 1_420_000, 'raw_text': '啊', 'accepted_text': '啊', 'lang': 'zh', 'words': [], 'alignment_status': 'segment'},
            {'id': 'next', 'start_us': 1_420_000, 'end_us': 2_600_000, 'raw_text': '救我', 'accepted_text': '救我', 'lang': 'zh', 'words': [], 'alignment_status': 'segment'}]
    transcript = store.create('transcript', {'source_id': source, 'audio_track_id': 'default', 'asset_ids': [asset['id']], 'cues': cues}, p)
    words = [('真', 240000, 400000), ('的', 400000, 560000), ('!', 560000, 700000), ('是', 700000, 800000), ('我', 800000, 1339000), ('!', 1339000, 1380000)]
    aligned = [{**cues[0], 'alignment_status': 'word', 'words': [{'id': f'w{i}', 'text': t, 'start_us': s, 'end_us': e, 'lang': 'zh'} for i, (t, s, e) in enumerate(words)]},
               cues[1], cues[2]]
    alignment = store.create('alignment', {'transcript_revision': transcript['id'], 'source_id': source, 'audio_track_id': 'default',
        'asset_ids': [asset['id']], 'map_revisions': ['map'], 'content_hashes': [digest], 'cues': aligned}, p)
    body = {'source_id': source, 'ranges': [{'start_us': 0, 'end_us': 3000000}], 'transcript_revision': transcript['id'],
            'sentences_per_cue': 1, 'keep_punctuation': False, 'subtitle_timebase': 'sequence', 'grouping': 'merge'}
    return store, p, transcript, alignment, body


def test_preview_is_the_same_srt_the_export_writes(tmp_path):
    from app.studio.api import create_app
    app = create_app(tmp_path / 'state', start_workers=False)
    store, p, transcript, alignment, body = build_project(tmp_path)
    with TestClient(app) as c:
        c.get('/v1/session')
        pending = c.post(f'/v1/projects/{p}/subtitles/preview', json=body)
        assert pending.status_code == 200, pending.text
        assert pending.json()['alignment'] == 'pending' and pending.json()['alignment_revision'] is None  # 還沒對齊：句子時間，並說明
        align = store.submit(p, {'kind': 'align', 'transcript_revision': transcript['id']}, 'gpu', key=f"align:{transcript['id']}")
        store.finish(align['id'], 'succeeded', {'alignment_revision': alignment['id']})
        preview = c.post(f'/v1/projects/{p}/subtitles/preview', json=body).json()
        assert c.post(f'/v1/projects/{p}/subtitles/preview', json={**body, 'transcript_revision': 'transcript_missing'}).status_code == 404
    assert preview['alignment'] == 'word' and preview['alignment_revision'] == alignment['id']
    export = store.submit(p, {'kind': 'export', **body, 'formats': ['srt'], 'alignment_policy': 'allow_segment', 'await_alignment': True})
    result = Worker(store, export).run()
    caption = next(a for a in result['artifacts'] if a['kind'] == 'srt')
    assert store.artifact_path(caption['id']).read_text(encoding='utf-8') == preview['srt']  # 逐字相同
    first = preview['entries'][0]
    assert first['index'] == 1 and first['start'] == '00:00:00,240' and first['end'] == '00:00:00,700' and first['text'] == '真的'
    assert first['source_start_us'] == 240000  # 播放器用來跳到這一則
    merged = preview['entries'][-1]
    assert merged['text'] == '啊 救我' and merged['start'] == '00:00:01,400'


def test_export_and_preview_report_subtitle_quality_checks(tmp_path):
    """M4-C2（2026-09-20）：匯出清單與預覽都要附字幕品質檢查（重疊、過短、過長、太快、相鄰重複、草稿有話卻沒字幕）。"""
    from app.studio import subtitles as sub
    from app.studio.api import create_app
    entries = [{'id': 'a', 'start_us': 0, 'end_us': 700_000, 'text': '大家好', 'origin_cue_ids': ['c1'], 'warnings': [], 'sequence_item_id': 'clip'},
               {'id': 'b', 'start_us': 600_000, 'end_us': 9_000_000, 'text': '今天玩遊戲' * 6, 'origin_cue_ids': ['c2'], 'warnings': [], 'sequence_item_id': 'clip'},
               {'id': 'c', 'start_us': 9_000_000, 'end_us': 9_200_000, 'text': '好快好快好快好快', 'origin_cue_ids': ['c3'], 'warnings': ['merged_short_cue'], 'sequence_item_id': 'clip'}]
    # 精修過的逐字稿：句子 id 是新的，origin_cue_ids 才是字幕帶著的草稿 id（真專案 49 句曾被全數誤判為漏字）
    cues = [{'id': 'r1', 'origin_cue_ids': ['c1'], 'text': '大家好'}, {'id': 'c2', 'text': '今天玩遊戲'},
            {'id': 'c3', 'text': '好快'}, {'id': 'c4', 'text': '被漏掉的一句'}]
    report = sub.quality_report(entries, cues)
    assert report['entries'] == 3 and report['overlaps'] == 1 and report['long_display'] == 1
    assert report['short_display'] == 1 and report['fast_entries'] == 1 and report['max_chars_per_sec'] >= 40
    assert report['missing_cue_ids'] == ['c4'] and report['warnings']['merged_short_cue'] == 1
    assert sub.quality_report([], [])['entries'] == 0

    app = create_app(tmp_path / 'state', start_workers=False)
    store, p, transcript, alignment, body = build_project(tmp_path)
    with TestClient(app) as c:
        c.get('/v1/session')
        preview = c.post(f'/v1/projects/{p}/subtitles/preview', json=body).json()
    assert preview['quality']['entries'] == len(preview['entries']) and preview['quality']['overlaps'] == 0
    export = store.submit(p, {'kind': 'export', **body, 'formats': ['srt'], 'alignment_policy': 'allow_segment'})
    result = Worker(store, export).run()
    caption = next(a for a in result['artifacts'] if a['kind'] == 'srt')
    assert caption['provenance']['quality']['entries'] == len(preview['entries'])
    assert result['manifest']['subtitle_quality'][0]['overlaps'] == 0
