"""同一素材第二次 analyze（例如換精修引擎重跑）不得因分類子工作的冪等鍵撞到不同內容而失敗；分類結果應重用。
（M1-3 真實重現：B 路徑重跑時 IDEMPOTENCY_CONFLICT「同一識別碼不可提交不同內容」。）"""
import hashlib
from pathlib import Path

from app.studio.store import Store
from app.studio.worker import Worker


def prepare(tmp_path, monkeypatch):
    path = tmp_path / 'audio.wav'
    path.write_bytes(b'RIFF' + b'\0' * 64)
    store = Store(tmp_path / 'state')
    project = store.create('project', {'name': '重跑分析'})['id']
    source = store.create('source', {'kind': 'local', 'title': 'audio.wav'}, project)['id']
    asset = store.create('asset', {'source_id': source, 'path': str(path), 'map_revision': 'm', 'duration_us': 2000000,
        'content_hash': hashlib.sha256(path.read_bytes()).hexdigest(), 'audio_track_id': 'default',
        'source_map': [{'source_start_us': 0, 'source_end_us': 2000000}]}, project)

    def fake_ffmpeg(argv, timeout):
        Path(argv[-1]).write_bytes(b'RIFF' + b'\0' * 32)
    monkeypatch.setattr('app.studio.media._run', fake_ffmpeg)

    def fake_draft(pcm, model='turbo', device='auto', language_policy='auto_ja_zh_en', language_hint=None, progress=None):
        cue = dict(id='c1', start_us=100000, end_us=900000, raw_text='測試', normalized_text='測試', accepted_text='測試', lang='zh',
                   words=[], word_ids=[], alignment_status='segment', review_flags=[])
        return dict(cues=[cue], settings=dict(engine='faster-whisper', model=model, device=device))
    monkeypatch.setattr('app.studio.asr.draft', fake_draft)
    return store, project, source, asset


def run_analyze(store, project, source, asset, **extra):
    body = {'kind': 'analyze', 'source_id': source, 'asset_ids': [asset['id']], 'profile': 'draft', 'music_policy': 'conservative', **extra}
    job = store.submit(project, body)
    return Worker(store, job).analyze()


def test_second_analyze_on_same_asset_reuses_classification_instead_of_conflicting(tmp_path, monkeypatch):
    store, project, source, asset = prepare(tmp_path, monkeypatch)
    first = run_analyze(store, project, source, asset, model='turbo')
    second = run_analyze(store, project, source, asset, model='large-v3')  # 不同設定 → 分析快取未命中 → 再次送出分類子工作
    assert first['classification_job_ids'] and second['classification_job_ids'] == first['classification_job_ids']
    listing = store.jobs(project, limit=50)
    items = listing['items'] if isinstance(listing, dict) else listing
    assert len([job for job in items if job['kind'] == 'classify']) == 1, '同一素材只應有一個分類工作'
    body = store.job(first['classification_job_ids'][0])['body']
    assert Path(body['pcm_path']).is_file(), '分類用的音訊放在與工作目錄無關的固定位置，才能讓同一素材的分類重用'
    assert str(tmp_path / 'state' / 'jobs') not in body['pcm_path']


def test_legacy_classification_record_does_not_block_reanalyze(tmp_path, monkeypatch):
    """修正前的紀錄：同一素材的 classify 鍵 ast-v1 綁著工作目錄裡的 pcm_path；修正後重跑不得再撞 IDEMPOTENCY_CONFLICT（2026-09-17 真實重現）。"""
    store, project, source, asset = prepare(tmp_path, monkeypatch)
    legacy = store.submit(project, {'kind': 'classify', 'source_id': source, 'asset_id': asset['id'],
        'pcm_path': str(tmp_path / 'state' / 'jobs' / 'job_old' / f"{asset['id']}.wav"), 'timeout_sec': 600}, 'cpu', key=f"classify:{asset['id']}:ast-v1")
    result = run_analyze(store, project, source, asset, model='turbo')
    assert result['classification_job_ids'] and result['classification_job_ids'] != [legacy['id']]
    again = run_analyze(store, project, source, asset, model='large-v3')
    assert again['classification_job_ids'] == result['classification_job_ids'], '新制鍵下同一素材仍重用'


def test_draft_cues_record_the_asset_they_came_from(tmp_path, monkeypatch):
    store, project, source, asset = prepare(tmp_path, monkeypatch)
    job = store.submit(project, {'kind': 'analyze', 'source_id': source, 'asset_ids': [asset['id']], 'music_policy': 'off'}, 'gpu')
    result = Worker(store, job).analyze()
    cues = store.get(result['transcript_revision'], 'transcript')['cues']
    assert cues and all(c['asset_id'] == asset['id'] for c in cues)


def test_analyze_queues_word_alignment_automatically(tmp_path, monkeypatch):
    """轉錄輸出時就要逐詞對齊：analyze 完成後自動排 align 子工作（同一逐字稿版本只排一次）；auto_align=False 可關。"""
    store, project, source, asset = prepare(tmp_path, monkeypatch)
    job = store.submit(project, {'kind': 'analyze', 'source_id': source, 'asset_ids': [asset['id']], 'music_policy': 'off'}, 'gpu')
    result = Worker(store, job).analyze()
    aligns = [j for j in store.jobs(project, 50)['items'] if j['kind'] == 'align']
    assert len(aligns) == 1 and aligns[0]['body']['transcript_revision'] == result['transcript_revision']
    assert aligns[0]['resource'] == 'gpu' and aligns[0]['parent_id'] == job['id']
    assert result['align_job_id'] == aligns[0]['id']
    # 同一版本再分析（快取命中）不重複排
    again = store.submit(project, {'kind': 'analyze', 'source_id': source, 'asset_ids': [asset['id']], 'music_policy': 'off'}, 'gpu', key='second')
    Worker(store, again).analyze()
    assert len([j for j in store.jobs(project, 50)['items'] if j['kind'] == 'align']) == 1
    # 關閉自動對齊
    (tmp_path / 'b').mkdir()
    store2, project2, source2, asset2 = prepare(tmp_path / 'b', monkeypatch)
    job2 = store2.submit(project2, {'kind': 'analyze', 'source_id': source2, 'asset_ids': [asset2['id']], 'music_policy': 'off', 'auto_align': False}, 'gpu')
    result2 = Worker(store2, job2).analyze()
    assert 'align_job_id' not in result2 and not [j for j in store2.jobs(project2, 50)['items'] if j['kind'] == 'align']


def test_transcribing_again_with_another_refine_model_reuses_the_draft(tmp_path, monkeypatch):
    """2026-09-21 真跑：同一份 10 分鐘音訊換精修模型重新轉錄，每次都重跑草稿（46–66 秒）。草稿只看音訊與草稿設定，
    與精修模型無關 → 同一份音訊、同樣的草稿設定就沿用上一次的草稿（精修照跑）；改了轉錄術語提示才重跑草稿。"""
    store, project, source, asset = prepare(tmp_path, monkeypatch)
    import app.studio.asr as asr
    real = asr.draft
    calls = []

    def counting(*args, **kwargs):
        calls.append(kwargs.get('hints'))
        return real(*args, **{k: v for k, v in kwargs.items() if k != 'hints'})
    monkeypatch.setattr('app.studio.asr.draft', counting)
    first = run_analyze(store, project, source, asset, music_policy='off')
    scope = f'transcript:{source}:default'
    # 精修（或人工修改）之後，目前版本換了 → 以前的快取就失效了
    refined = store.get(first['transcript_revision'], 'transcript')
    store.revise(scope, 'transcript', {**refined, 'cues': [dict(c, id='refined', accepted_text='精修後') for c in refined['cues']]},
                 first['transcript_revision'], project, initial=None)
    job = store.submit(project, {'kind': 'analyze', 'source_id': source, 'asset_ids': [asset['id']], 'profile': 'draft', 'music_policy': 'off'},
                       key='again')
    second = Worker(store, job).analyze()
    assert len(calls) == 1  # 草稿沒有重跑
    cues = store.get(second['transcript_revision'], 'transcript')['cues']
    assert [c['accepted_text'] for c in cues] == ['測試'] and cues[0]['id'] not in ('c1', 'refined')  # 草稿內容、新的句子識別碼
    assert any(e['type'] == 'cache.hit' and e['payload'].get('kind') == 'draft' for e in store.events(job['id']))
    run_analyze(store, project, source, asset, music_policy='off', asr_hints='小尹')
    assert len(calls) == 2  # 換了轉錄術語提示：草稿要重跑
