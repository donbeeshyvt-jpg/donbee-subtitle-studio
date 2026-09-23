"""2026-09-21 真瀏覽器（模仿使用者）：下載範圍填「5:00–10:00」與「9:00–15:00」兩段，重疊的部分被合併成一個 5:00–15:00 的檔，
畫面上完全沒說 → 使用者以為少了一段。合併本身是對的（同一段不下載、轉錄兩次），但結果要講清楚合併成什麼、要分開怎麼做。"""
from app.studio import media
from app.studio.store import Store
from app.studio.worker import Worker


def run_acquire(tmp_path, monkeypatch, ranges):
    store = Store(tmp_path / 'state')
    project = store.create('project', {'name': '範圍'})['id']
    local = tmp_path / 'input.wav'
    local.write_bytes(b'audio')
    source = store.create('source', {'kind': 'local', 'path': str(local), 'title': 'input.wav'}, project)
    store.revise(f"source:{source['id']}", 'source_metadata', {'duration_us': 1_200_000_000}, None, project, initial=None)
    cut = []

    def fake_cut(path, target, start_us, end_us, mode, kind):
        cut.append((start_us, end_us))
        return {'path': str(target), 'start_us': start_us, 'end_us': end_us}
    monkeypatch.setattr(media, 'cut_media', fake_cut)
    monkeypatch.setattr(Worker, 'register_asset', lambda self, src, record, kind: {'id': f"asset_{record['start_us']}", 'path': record['path']})
    job = store.submit(project, {'kind': 'acquire', 'source_id': source['id'], 'ranges': ranges, 'boundary_policy': 'accurate'})
    return Worker(store, job).acquire(), cut


def test_overlapping_ranges_are_merged_and_the_result_says_so(tmp_path, monkeypatch):
    m = 60_000_000
    result, cut = run_acquire(tmp_path, monkeypatch, [{'start_us': 5 * m, 'end_us': 10 * m}, {'start_us': 9 * m, 'end_us': 15 * m}])
    assert cut == [(5 * m, 15 * m)]  # 重疊的一分鐘只下載一次
    assert result['requested_ranges'] == [{'start_us': 5 * m, 'end_us': 10 * m}, {'start_us': 9 * m, 'end_us': 15 * m}]
    assert result['downloaded_ranges'] == [{'start_us': 5 * m, 'end_us': 15 * m}]
    notice = result['notice']
    assert '2 段' in notice and '1 段' in notice and '00:05:00' in notice and '00:15:00' in notice
    assert '加入片段' in notice  # 告訴使用者要分開的片段怎麼做


def test_separate_ranges_are_not_merged_and_need_no_notice(tmp_path, monkeypatch):
    m = 60_000_000
    result, cut = run_acquire(tmp_path, monkeypatch, [{'start_us': 1 * m, 'end_us': 2 * m}, {'start_us': 4 * m, 'end_us': 5 * m}])
    assert cut == [(1 * m, 2 * m), (4 * m, 5 * m)]
    assert 'notice' not in result and result['downloaded_ranges'] == result['requested_ranges']


# 2026-09-21 真瀏覽器：勾「下載完成後自動轉錄」時，網頁選的精修模型與轉錄術語提示沒有帶到自動排的轉錄 → 一律用預設
def test_auto_transcribe_after_download_uses_the_chosen_model_and_hints(tmp_path, monkeypatch):
    m = 60_000_000
    store = Store(tmp_path / 'state')
    (store.root / 'config.json').write_text('{"default_asr_model": "large-v3"}', encoding='utf-8')
    project = store.create('project', {'name': '自動轉錄'})['id']
    local = tmp_path / 'input.wav'
    local.write_bytes(b'audio')
    source = store.create('source', {'kind': 'local', 'path': str(local), 'title': 'input.wav'}, project)
    store.revise(f"source:{source['id']}", 'source_metadata', {'duration_us': 20 * m}, None, project, initial=None)
    monkeypatch.setattr(media, 'cut_media', lambda path, target, s, e, mode, kind: {'path': str(target), 'start_us': s, 'end_us': e})
    monkeypatch.setattr(Worker, 'register_asset', lambda self, src, record, kind: {'id': f"asset_{record['start_us']}", 'path': record['path'],
                                                                                 'audio_track_id': 'default'})
    chosen = {'kind': 'analyze', 'profile': 'quality', 'asr_model': 'breeze-asr-25', 'asr_hints': '小尹, 初代'}
    job = store.submit(project, {'kind': 'acquire', 'source_id': source['id'], 'ranges': [{'start_us': 0, 'end_us': m}], 'follow_up': chosen})
    result = Worker(store, job).acquire()
    child = store.job(result['child_job_ids'][0])
    assert child['body']['asr_model'] == 'breeze-asr-25' and child['body']['asr_hints'] == '小尹, 初代'
    # 沒選模型：把實際會用的預設寫進工作內容（之後看得出用了哪個）
    job = store.submit(project, {'kind': 'acquire', 'source_id': source['id'], 'ranges': [{'start_us': 2 * m, 'end_us': 3 * m}],
                                 'follow_up': {'kind': 'analyze', 'profile': 'quality'}})
    result = Worker(store, job).acquire()
    assert store.job(result['child_job_ids'][0])['body']['asr_model'] == 'large-v3'


def test_api_checks_remote_consent_for_auto_transcribe_before_downloading(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.studio.api import create_app
    monkeypatch.delenv('ELEVENLABS_API_KEY', raising=False)
    with TestClient(create_app(tmp_path / 'state', start_workers=False)) as c:
        c.get('/v1/session')
        project = c.post('/v1/projects', json={'name': '下載後遠端轉錄'}).json()['project_id']
        source = c.post(f'/v1/projects/{project}/sources', json={'kind': 'youtube', 'url': 'https://www.youtube.com/watch?v=Yn2mE6_tMC8'}).json()['source_id']
        body = {'kind': 'acquire', 'source_id': source, 'ranges': [{'start_us': 0, 'end_us': 60_000_000}],
                'follow_up': {'kind': 'analyze', 'profile': 'quality', 'asr_model': 'elevenlabs'}}
        refused = c.post(f'/v1/projects/{project}/jobs', json=body)
        assert refused.status_code == 422 and refused.json()['error']['code'] == 'REMOTE_CONSENT_REQUIRED'
        no_key = c.post(f'/v1/projects/{project}/jobs', json={**body, 'follow_up': {**body['follow_up'], 'remote_consent': True}})
        assert no_key.status_code == 409 and no_key.json()['error']['code'] == 'MODEL_NOT_INSTALLED'
        local = c.post(f'/v1/projects/{project}/jobs', json={**body, 'follow_up': {'kind': 'analyze', 'profile': 'quality', 'asr_model': 'large-v3'}})
        assert local.status_code == 202 and local.json()['body']['follow_up']['asr_model'] == 'large-v3'
