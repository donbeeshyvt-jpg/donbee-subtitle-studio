"""真 ASGI 路由整合：工作非同步、存取與版本契約。"""
from fastapi.testclient import TestClient
from app.studio.api import create_app


def client(tmp_path):
    app = create_app(tmp_path, start_workers=False)
    result = TestClient(app)
    assert result.get('/v1/session').status_code == 200
    return result


def test_session_and_origin_protection(tmp_path):
    c = TestClient(create_app(tmp_path, start_workers=False))
    assert c.get('/v1/projects').status_code == 401
    # 2026-09-19：使用者看不懂「尚未建立本機工作階段」——訊息要說清楚原因與該做什麼（網頁會自動重連，仍失敗才重新整理）
    denied = c.get('/v1/projects', cookies={'studio_session': '1.expired'}).json()['error']
    assert denied['code'] == 'ACCESS_DENIED' and '過期' in denied['message'] and '重新整理' in denied['message']
    assert c.get('/v1/session', headers={'Origin': 'https://evil.example'}).status_code == 403
    assert c.get('/v1/health', headers={'Host': 'evil.example'}).status_code == 403


def test_project_source_job_idempotency_and_revision(tmp_path):
    c = client(tmp_path)
    project = c.post('/v1/projects', json={'name': '冬比測試'}).json()['project_id']
    source = c.post(f'/v1/projects/{project}/sources', json={'kind': 'youtube',
        'url': 'https://www.youtube.com/live/Yn2mE6_tMC8'}).json()['source_id']
    endpoint = f'/v1/projects/{project}/jobs'
    body = {'kind': 'probe', 'source_id': source}
    result = c.post(endpoint, json=body, headers={'Idempotency-Key': 'one'})
    assert result.status_code == 202
    assert c.post(endpoint, json=body, headers={'Idempotency-Key': 'one'}).json()['job_id'] == result.json()['job_id']
    assert c.post(endpoint, json={**body, 'quality': 'preview'}, headers={'Idempotency-Key': 'one'}).status_code == 409
    request = {'source_id': source, 'base_revision': 'seq_00', 'items': [
        {'id':'a','start_us': 6600000000, 'end_us':6660000000, 'name':'一'}]}
    assert c.put(f'/v1/projects/{project}/sequence', json=request).status_code == 200
    assert c.put(f'/v1/projects/{project}/sequence', json=request).status_code == 409
    job_id = result.json()['job_id']
    assert c.post(f'/v1/jobs/{job_id}/cancel').json()['status'] == 'cancelled'
    assert c.get(f'/v1/jobs/{job_id}/events').headers['content-type'].startswith('text/event-stream')


def test_ranges_and_unknown_fields_rejected(tmp_path):
    c = client(tmp_path)
    p = c.post('/v1/projects', json={'name': 'test'}).json()['project_id']
    source = c.post(f'/v1/projects/{p}/sources', json={'kind':'youtube',
        'url':'https://youtu.be/Yn2mE6_tMC8'}).json()['source_id']
    for value in [-1, True, 0.1]:
        assert c.post(f'/v1/projects/{p}/jobs', json={'kind':'acquire','source_id':source,
            'ranges':[{'start_us':value,'end_us':1000}]}).status_code == 422
    assert c.post(f'/v1/projects/{p}/jobs', json={'kind':'probe','source_id':source,'shell':'whoami'}).status_code == 422


def test_cross_project_reference_is_rejected(tmp_path):
    c = client(tmp_path)
    p1 = c.post('/v1/projects', json={'name':'one'}).json()['project_id']
    p2 = c.post('/v1/projects', json={'name':'two'}).json()['project_id']
    s = c.post(f'/v1/projects/{p1}/sources', json={'kind':'youtube','url':'https://youtu.be/Yn2mE6_tMC8'}).json()['source_id']
    assert c.post(f'/v1/projects/{p2}/jobs', json={'kind':'probe','source_id':s}).status_code == 404
