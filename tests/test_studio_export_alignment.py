"""匯出時提供 alignment_revision 就應使用逐詞時間；allow_segment 只代表沒對齊到的句子可退回句時間。
（M2-4 真實重現：對齊 354/373 句後，allow_segment 匯出的 SRT 時間與未對齊版本完全相同、仍標 coarse_alignment。）"""
import hashlib

from app.studio.store import Store
from app.studio.worker import Worker


def build(tmp_path, policy):
    path = tmp_path / 'audio.wav'
    path.write_bytes(b'RIFF' + b'\0' * 64)
    store = Store(tmp_path / 'state')
    p = store.create('project', {'name': '對齊匯出'})['id']
    source = store.create('source', {'kind': 'local', 'path': str(path)}, p)['id']
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    asset = store.create('asset', {'source_id': source, 'kind': 'audio', 'path': str(path), 'map_revision': 'map', 'content_hash': digest,
        'source_map': [{'id': 'map', 'source_start_us': 0, 'source_end_us': 3000000, 'asset_start_us': 0, 'asset_end_us': 3000000, 'status': 'verified'}]}, p)
    seq = store.create('sequence', {'source_id': source, 'items': [{'id': 'clip', 'kind': 'clip', 'start_us': 0, 'end_us': 3000000}]}, p)
    cue = {'id': 'cue', 'start_us': 240000, 'end_us': 1380000, 'raw_text': '真的!是我!是我!', 'accepted_text': '真的!是我!是我!', 'lang': 'zh',
           'words': [], 'alignment_status': 'segment'}
    transcript = store.create('transcript', {'source_id': source, 'audio_track_id': 'default', 'asset_ids': [asset['id']], 'cues': [cue]}, p)
    # 2026-09-20：過短字幕（< 0.3 秒）會併進鄰句，字長改成實際語速（每則 ≥ 0.3 秒），才看得出用的是逐詞時間
    words = [('真', 240000, 400000), ('的', 400000, 560000), ('!', 560000, 700000), ('是', 700000, 800000), ('我', 800000, 900000),
             ('!', 900000, 1000000), ('是', 1000000, 1100000), ('我', 1100000, 1339000), ('!', 1339000, 1380000)]
    aligned = {**cue, 'alignment_status': 'word',
               'words': [{'id': f'w{i}', 'text': t, 'start_us': s, 'end_us': e, 'lang': 'zh'} for i, (t, s, e) in enumerate(words)]}
    alignment = store.create('alignment', {'transcript_revision': transcript['id'], 'source_id': source, 'audio_track_id': 'default',
        'asset_ids': [asset['id']], 'map_revisions': ['map'], 'content_hashes': [digest], 'cues': [aligned]}, p)
    job = store.submit(p, {'kind': 'export', 'sequence_revision': seq['id'], 'transcript_revision': transcript['id'],
        'alignment_revision': alignment['id'], 'alignment_policy': policy, 'formats': ['srt'], 'sentences_per_cue': 1})
    result = Worker(store, job).run()
    caption = next(a for a in result['artifacts'] if a['kind'] == 'srt')
    srt = store.artifact_path(caption['id']).read_text(encoding='utf-8')
    return caption, srt


def test_export_uses_provided_alignment_even_with_allow_segment(tmp_path):
    caption, srt = build(tmp_path, 'allow_segment')
    assert '00:00:00,240 --> 00:00:00,700' in srt and '00:00:00,700 --> 00:00:01,000' in srt, srt
    assert 'coarse_alignment' not in caption['provenance']['warnings']


def test_export_require_word_still_uses_alignment(tmp_path):
    caption, srt = build(tmp_path, 'require_word')
    assert '00:00:00,240 --> 00:00:00,700' in srt
    assert 'coarse_alignment' not in caption['provenance']['warnings']


# ---- 2026-09-19：匯出要等逐詞對齊（編輯後馬上按匯出，不能悄悄退回句子時間）----
import threading
import time


def fixture(tmp_path, with_assets=True, track='default'):
    path = tmp_path / 'audio.wav'
    path.write_bytes(b'RIFF' + b'\0' * 64)
    store = Store(tmp_path / 'state')
    p = store.create('project', {'name': '等對齊'})['id']
    source = store.create('source', {'kind': 'local', 'path': str(path), 'title': 'audio.wav'}, p)['id']
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    asset = store.create('asset', {'source_id': source, 'kind': 'audio', 'path': str(path), 'map_revision': 'map', 'content_hash': digest,
        'source_map': [{'id': 'map', 'source_start_us': 0, 'source_end_us': 3000000, 'asset_start_us': 0, 'asset_end_us': 3000000, 'status': 'verified'}]}, p)
    cue = {'id': 'cue', 'start_us': 240000, 'end_us': 1380000, 'raw_text': '真的!是我!', 'accepted_text': '真的!是我!', 'lang': 'zh', 'words': [], 'alignment_status': 'segment'}
    transcript = store.create('transcript', {'source_id': source, 'audio_track_id': track, 'asset_ids': [asset['id']] if with_assets else [], 'cues': [cue]}, p)
    words = [('真', 240000, 400000), ('的', 400000, 560000), ('!', 560000, 700000), ('是', 700000, 800000), ('我', 800000, 1339000), ('!', 1339000, 1380000)]
    aligned = {**cue, 'alignment_status': 'word', 'words': [{'id': f'w{i}', 'text': t, 'start_us': s, 'end_us': e, 'lang': 'zh'} for i, (t, s, e) in enumerate(words)]}
    alignment = store.create('alignment', {'transcript_revision': transcript['id'], 'source_id': source, 'audio_track_id': track,
        'asset_ids': [asset['id']], 'map_revisions': ['map'], 'content_hashes': [digest], 'cues': [aligned]}, p)
    body = {'kind': 'export', 'source_id': source, 'ranges': [{'start_us': 0, 'end_us': 3000000}], 'transcript_revision': transcript['id'],
            'alignment_policy': 'allow_segment', 'formats': ['srt'], 'sentences_per_cue': 1}
    return store, p, transcript, alignment, body


def srt_of(store, result):
    caption = next(a for a in result['artifacts'] if a['kind'] == 'srt')
    return store.artifact_path(caption['id']).read_text(encoding='utf-8')


def finish_later(store, job_id, status, result=None, delay=0.4):
    def work():
        time.sleep(delay)
        store.finish(job_id, status, result, None if status == 'succeeded' else {'code': 'GPU_BUSY', 'message': '顯示卡忙碌'})
    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread


def test_export_waits_for_the_running_alignment_and_uses_word_timing(tmp_path):
    store, p, transcript, alignment, body = fixture(tmp_path)
    align = store.submit(p, {'kind': 'align', 'transcript_revision': transcript['id']}, 'gpu', key=f"align:{transcript['id']}")
    thread = finish_later(store, align['id'], 'succeeded', {'alignment_revision': alignment['id']})
    started = time.monotonic()
    result = Worker(store, store.submit(p, {**body, 'await_alignment': True})).run()
    thread.join()
    assert time.monotonic() - started >= 0.3  # 真的有等
    assert '00:00:00,240 --> 00:00:00,700' in srt_of(store, result)
    assert result['manifest']['alignment_revision'] == alignment['id']
    assert 'alignment_unavailable_segment_timing' not in result['manifest']['warnings']


def test_export_uses_an_already_finished_alignment_without_being_told_its_id(tmp_path):
    store, p, transcript, alignment, body = fixture(tmp_path)
    align = store.submit(p, {'kind': 'align', 'transcript_revision': transcript['id']}, 'gpu', key=f"align:{transcript['id']}")
    store.finish(align['id'], 'succeeded', {'alignment_revision': alignment['id']})
    result = Worker(store, store.submit(p, {**body, 'await_alignment': True})).run()
    assert '00:00:00,240 --> 00:00:00,700' in srt_of(store, result) and result['manifest']['alignment_revision'] == alignment['id']


def test_export_falls_back_to_sentence_timing_with_a_warning_when_alignment_fails(tmp_path):
    store, p, transcript, alignment, body = fixture(tmp_path)
    align = store.submit(p, {'kind': 'align', 'transcript_revision': transcript['id']}, 'gpu', key=f"align:{transcript['id']}")
    thread = finish_later(store, align['id'], 'failed')
    result = Worker(store, store.submit(p, {**body, 'await_alignment': True})).run()
    thread.join()
    srt = srt_of(store, result)
    # 句子時間：沒有逐詞時間時，同一句被標點切成兩則，時間按字數比例分（2026-09-20 起不再兩則共用同一個時間）
    assert '00:00:00,240 --> 00:00:00,810' in srt and '00:00:00,810 --> 00:00:01,510' in srt, srt  # 第二則延長到最短顯示 0.7 秒
    assert result['manifest']['alignment_revision'] is None
    assert 'alignment_unavailable_segment_timing' in result['manifest']['warnings']


def test_export_queues_the_missing_alignment_itself_when_asked(tmp_path):
    # CLI／API 直接改完字就匯出：沒有人排過對齊 → 匯出自己排一個（與網頁同一個冪等鍵）再等它
    store, p, transcript, alignment, body = fixture(tmp_path)

    def gpu_worker():
        for _ in range(100):
            queued = [j for j in store.jobs(p, 50)['items'] if j['kind'] == 'align']
            if queued:
                store.finish(queued[0]['id'], 'succeeded', {'alignment_revision': alignment['id']})
                return
            time.sleep(0.05)
    thread = threading.Thread(target=gpu_worker, daemon=True)
    thread.start()
    result = Worker(store, store.submit(p, {**body, 'await_alignment': True})).run()
    thread.join()
    aligns = [j for j in store.jobs(p, 50)['items'] if j['kind'] == 'align']
    assert len(aligns) == 1 and aligns[0]['body']['transcript_revision'] == transcript['id']
    assert '00:00:00,240 --> 00:00:00,700' in srt_of(store, result)


def test_export_does_not_wait_without_the_flag_or_when_alignment_cannot_apply(tmp_path):
    store, p, transcript, alignment, body = fixture(tmp_path)
    store.submit(p, {'kind': 'align', 'transcript_revision': transcript['id']}, 'gpu', key=f"align:{transcript['id']}")  # 一直排隊中
    started = time.monotonic()
    result = Worker(store, store.submit(p, body)).run()  # 沒帶 await_alignment：維持原行為
    assert time.monotonic() - started < 5 and '00:00:00,240 --> 00:00:00,810' in srt_of(store, result)
    # 來源字幕軌／沒有素材的逐字稿：沒得對齊，不排、不等
    for index, (with_assets, track) in enumerate(((False, 'default'), (True, 'source_subtitles'))):
        sub = tmp_path / f'case{index}'
        sub.mkdir()
        store, p, transcript, alignment, body = fixture(sub, with_assets, track)
        result = Worker(store, store.submit(p, {**body, 'await_alignment': True})).run()
        assert not [j for j in store.jobs(p, 50)['items'] if j['kind'] == 'align']
        assert '00:00:00,240 --> 00:00:00,810' in srt_of(store, result)


def test_exported_srt_gives_each_subtitle_time_to_be_read(tmp_path):
    # M4-B3：匯出的 SRT 不再出現只顯示 0.04 秒的字幕（往後延到 0.7 秒，不壓到下一則、不超出輸出範圍）
    path = tmp_path / 'audio.wav'
    path.write_bytes(b'RIFF' + b'\0' * 64)
    store = Store(tmp_path / 'state')
    p = store.create('project', {'name': '最短顯示'})['id']
    source = store.create('source', {'kind': 'local', 'path': str(path), 'title': 'audio.wav'}, p)['id']
    cues = [{'id': 'one', 'start_us': 1_000_000, 'end_us': 1_040_000, 'raw_text': '第一句', 'accepted_text': '第一句', 'lang': 'zh', 'words': [], 'alignment_status': 'segment'},
            {'id': 'two', 'start_us': 3_000_000, 'end_us': 3_040_000, 'raw_text': '第二句', 'accepted_text': '第二句', 'lang': 'zh', 'words': [], 'alignment_status': 'segment'}]
    transcript = store.create('transcript', {'source_id': source, 'audio_track_id': 'default', 'asset_ids': [], 'cues': cues}, p)
    job = store.submit(p, {'kind': 'export', 'source_id': source, 'ranges': [{'start_us': 0, 'end_us': 3_500_000}], 'transcript_revision': transcript['id'],
                           'formats': ['srt'], 'grouping': 'merge', 'subtitle_timebase': 'sequence', 'alignment_policy': 'allow_segment'})
    srt = srt_of(store, Worker(store, job).run())
    assert '00:00:01,000 --> 00:00:01,700' in srt
    assert '00:00:03,000 --> 00:00:03,500' in srt  # 最後一則不超出匯出範圍（3.5 秒）
