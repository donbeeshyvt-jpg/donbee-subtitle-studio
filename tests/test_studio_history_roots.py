"""使用者 2026-09-18：下載與輸出都要能指定本地資料夾；逐字稿從 WhisperX 輸出後的每一步修改（逐句編輯、LLM 套用、還原）都要有紀錄可匯出成 JSON。"""
import hashlib
from fastapi.testclient import TestClient

from app.studio.api import create_app
from app.studio.store import Store


def test_output_roots_can_be_added_listed_and_removed(tmp_path):
    with TestClient(create_app(tmp_path / 'state', start_workers=False)) as c:
        c.get('/v1/session')
        folder = tmp_path / '成品'
        folder.mkdir()
        created = c.post('/v1/output-roots', json={'path': str(folder), 'name': '成品資料夾'})
        assert created.status_code == 201, created.text
        roots = created.json()['items']
        assert roots[0]['name'] == '成品資料夾' and roots[0]['path'] == str(folder.resolve())
        # 2026-09-19：檔案實際放在 <選的資料夾>/subtitle_studio，清單要把這個位置告訴介面
        assert roots[0]['target'] == str(folder.resolve() / 'subtitle_studio')
        capabilities = c.get('/v1/capabilities').json()['output_roots']
        assert any(o['id'] == roots[0]['id'] and o['path'] == str(folder.resolve()) and o['name'] == '成品資料夾' for o in capabilities)
        # 不存在的資料夾：明確拒絕；create=true 才建立
        missing = c.post('/v1/output-roots', json={'path': str(tmp_path / 'nope')})
        assert missing.status_code == 422 and missing.json()['error']['code'] == 'OUTPUT_ROOT_MISSING'
        assert c.post('/v1/output-roots', json={'path': 'relative/dir'}).status_code == 422
        made = c.post('/v1/output-roots', json={'path': str(tmp_path / 'new'), 'create': True})
        assert made.status_code == 201 and (tmp_path / 'new').is_dir()
        # 同一路徑再加：回同一個 id，不重複
        again = c.post('/v1/output-roots', json={'path': str(folder)})
        assert again.status_code == 201 and sum(1 for o in again.json()['items'] if o['path'] == str(folder.resolve())) == 1
        assert c.delete(f"/v1/output-roots/{roots[0]['id']}").status_code == 200
        assert all(o['id'] != roots[0]['id'] for o in c.get('/v1/capabilities').json()['output_roots'])
        # 設定檔持久化（重新載入服務仍在）
    with TestClient(create_app(tmp_path / 'state', start_workers=False)) as c:
        c.get('/v1/session')
        assert any(o['path'] == str((tmp_path / 'new').resolve()) for o in c.get('/v1/capabilities').json()['output_roots'])


def test_transcript_edits_record_provenance_and_history_is_exportable(tmp_path):
    store = Store(tmp_path / 'state')
    project = store.create('project', {'name': '紀錄'})['id']
    source = store.create('source', {'kind': 'local', 'title': '素材'}, project)['id']
    cues = [{'id': 'c1', 'start_us': 0, 'end_us': 1000000, 'raw_text': '彈步遊戲', 'accepted_text': '彈步遊戲', 'words': []},
            {'id': 'c2', 'start_us': 2000000, 'end_us': 3000000, 'raw_text': '我在你', 'accepted_text': '我在你', 'words': []}]
    base = store.revise(f'transcript:{source}:default', 'transcript', {'source_id': source, 'audio_track_id': 'default', 'cues': cues,
        'settings': {'engine': 'faster-whisper', 'model': 'turbo'}, 'asset_ids': []}, None, project, initial=None)
    with TestClient(create_app(tmp_path / 'state', start_workers=False)) as c:
        c.get('/v1/session')
        first = c.post(f"/v1/projects/{project}/transcripts/{base['id']}/edits", json={
            'base_revision': base['id'], 'origin': 'llm_correction', 'job_id': 'job_x',
            'edits': [{'cue_id': 'c1', 'text': '彈幕遊戲'}]})
        assert first.status_code == 201, first.text
        rev1 = first.json()
        assert rev1['change']['origin'] == 'llm_correction' and rev1['change']['job_id'] == 'job_x'
        assert rev1['change']['edits'] == [{'cue_id': 'c1', 'before': '彈步遊戲', 'after': '彈幕遊戲'}]
        second = c.post(f"/v1/projects/{project}/transcripts/{rev1['id']}/edits", json={
            'base_revision': rev1['id'], 'edits': [{'cue_id': 'c2', 'text': '我跟你'}], 'note': '手動'})
        rev2 = second.json()
        assert rev2['change']['origin'] == 'manual' and rev2['change']['note'] == '手動'
        history = c.get(f"/v1/projects/{project}/transcripts/{rev2['id']}/history")
        assert history.status_code == 200, history.text
        data = history.json()
        assert data['current'] == rev2['id'] and data['base']['revision'] == base['id']
        assert data['base']['settings']['engine'] == 'faster-whisper' and data['base']['cue_count'] == 2
        assert [s['origin'] for s in data['steps']] == ['llm_correction', 'manual']
        assert data['steps'][0]['edits'][0]['after'] == '彈幕遊戲' and data['steps'][1]['edits'][0]['before'] == '我在你'
        assert data['total_edits'] == 2
        # 不合法的 origin 拒絕
        bad = c.post(f"/v1/projects/{project}/transcripts/{rev2['id']}/edits", json={'base_revision': rev2['id'], 'origin': 'hack', 'edits': []})
        assert bad.status_code == 422


def test_workbench_html_is_never_cached_so_reload_gets_the_new_build(tmp_path):
    """2026-09-18：使用者重新整理仍看到舊版面（瀏覽器快取了 index.html）。HTML 殼不可快取；帶雜湊檔名的 assets 可以。"""
    with TestClient(create_app(tmp_path / 'state', start_workers=False)) as c:
        page = c.get('/v2/')
        if page.status_code == 404:
            return  # 沒有建置前端的環境（CI）：略過
        assert page.status_code == 200 and 'text/html' in page.headers['content-type']
        assert 'no-store' in page.headers.get('cache-control', '') or 'no-cache' in page.headers.get('cache-control', '')
        home = c.get('/', follow_redirects=False)
        assert home.status_code in (302, 307) and 'no-store' in home.headers.get('cache-control', '')
