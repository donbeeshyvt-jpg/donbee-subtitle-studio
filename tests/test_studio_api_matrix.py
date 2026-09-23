"""M5-2：API／持久工作／CLI 整合矩陣 I01–I12（docs/TEST_PLAN_V2.md 第 4 節）的缺口補測（2026-09-20）。
每個測試名稱以案例編號開頭；既有測試已涵蓋的部分見 TEST_REPORT「M5-2 矩陣」。模型一律不載入。"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.studio.api import create_app
from app.studio.store import Store

SRC = Path(__file__).resolve().parents[1] / 'src'


def session(tmp_path, **kwargs):
    app = create_app(tmp_path / 'state', start_workers=False, **kwargs)
    client = TestClient(app)
    assert client.get('/v1/session').status_code == 200
    return app, client


def project_and_source(client):
    project = client.post('/v1/projects', json={'name': '矩陣'}).json()['project_id']
    source = client.post(f'/v1/projects/{project}/sources', json={'kind': 'youtube', 'url': 'https://www.youtube.com/watch?v=Yn2mE6_tMC8'}).json()['source_id']
    return project, source


# ---- I01 ----

def test_i01_health_answers_without_loading_any_model(tmp_path):
    code = ("import sys; from fastapi.testclient import TestClient; from app.studio.api import create_app; "
            f"c=TestClient(create_app(r'{tmp_path / 'state'}', start_workers=False)); r=c.get('/v1/health'); "
            "heavy=[m for m in ('torch','faster_whisper','whisperx','ctranslate2','transformers') if m in sys.modules]; "
            "print(r.status_code, heavy)")
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=120,
                         env={**os.environ, 'PYTHONPATH': str(SRC)}).stdout.strip().splitlines()[-1]
    assert out == '200 []', out


def test_i01_projects_and_jobs_survive_a_service_restart(tmp_path):
    app, client = session(tmp_path)
    project, source = project_and_source(client)
    job = client.post(f'/v1/projects/{project}/jobs', json={'kind': 'probe', 'source_id': source})
    assert job.status_code == 202 and job.json()['job_id']
    client.close()
    _, again = session(tmp_path)  # 同一資料根重新開服務
    assert any(p['project_id'] == project for p in again.get('/v1/projects').json()['items'])
    restored = again.get(f"/v1/jobs/{job.json()['job_id']}").json()
    assert restored['status'] == 'queued' and restored['body']['source_id'] == source and restored['project_id'] == project


def test_i01_openapi_covers_every_route_and_errors_share_one_envelope(tmp_path):
    app, client = session(tmp_path)
    paths = set(client.get('/openapi.json').json()['paths'])
    routes = {route.path for route in app.routes if getattr(route, 'path', '').startswith('/v1/')}
    assert routes and routes <= paths
    project, source = project_and_source(client)
    responses = [client.get('/v1/jobs/job_missing'),                                          # 不存在
                 client.post(f'/v1/projects/{project}/jobs', json={'kind': 'nope'}),               # 驗證失敗
                 client.put(f'/v1/projects/{project}/sequence', json={'base_revision': 'seq_99', 'source_id': source, 'items': []})]  # 版本衝突
    assert [r.status_code for r in responses] == [404, 422, 409]
    for response in responses:
        error = response.json()['error']
        assert set(error) == {'code', 'message', 'details', 'retryable', 'request_id'} and error['code'] and error['message']


# ---- I02／I10：同名上傳、失敗上傳 ----

def test_i02_i10_same_name_uploads_get_separate_files_and_bad_uploads_leave_nothing(tmp_path, monkeypatch):
    app, client = session(tmp_path)
    project = client.post('/v1/projects', json={'name': '上傳'}).json()['project_id']
    uploads = tmp_path / 'state' / 'uploads'
    first = client.post(f'/v1/projects/{project}/uploads', files={'file': ('same.wav', b'first-audio', 'audio/wav')})
    second = client.post(f'/v1/projects/{project}/uploads', files={'file': ('same.wav', b'second-audio!', 'audio/wav')})
    assert first.status_code == second.status_code == 201 and first.json()['source_id'] != second.json()['source_id']
    files = sorted(p.read_bytes() for p in uploads.iterdir())
    assert files == [b'first-audio', b'second-audio!']  # 兩份都在，不互相覆蓋
    # 超過容量：413，不留暫存檔、不建立來源
    app.state.config['max_upload_bytes'] = 10
    before = client.get(f'/v1/projects/{project}/sources').json()['items']
    too_big = client.post(f'/v1/projects/{project}/uploads', files={'file': ('big.wav', b'x' * 11, 'audio/wav')})
    assert too_big.status_code == 413 and too_big.json()['error']['code'] == 'UPLOAD_TOO_LARGE'
    unsupported = client.post(f'/v1/projects/{project}/uploads', files={'file': ('note.txt', b'x', 'text/plain')})
    assert unsupported.status_code == 422 and unsupported.json()['error']['code'] == 'UNSUPPORTED_MEDIA'
    app.state.config['max_upload_bytes'] = 1024 ** 3
    # 上傳中斷：讀到一半連線斷掉 → 暫存檔刪除，不發布
    from starlette.datastructures import UploadFile
    original = UploadFile.read
    state = {'calls': 0}

    async def broken(self, size=-1):
        state['calls'] += 1
        if state['calls'] > 1:
            raise ConnectionResetError('client went away')
        return await original(self, size)
    monkeypatch.setattr(UploadFile, 'read', broken)
    with pytest.raises(ConnectionResetError):
        client.post(f'/v1/projects/{project}/uploads', files={'file': ('cut.wav', b'y' * (3 * 1024 * 1024), 'audio/wav')})
    assert sorted(p.read_bytes() for p in uploads.iterdir()) == files and not list(uploads.glob('*.staging'))
    assert client.get(f'/v1/projects/{project}/sources').json()['items'] == before


def test_i02_idempotent_resend_creates_one_job_only(tmp_path):
    app, client = session(tmp_path)
    project, source = project_and_source(client)
    body = {'kind': 'probe', 'source_id': source}
    ids = {client.post(f'/v1/projects/{project}/jobs', json=body, headers={'Idempotency-Key': 'same'}).json()['job_id'] for _ in range(3)}
    assert len(ids) == 1 and len(client.get(f'/v1/projects/{project}/jobs').json()['items']) == 1


# ---- I03：SSE ----

def _events(text):
    return [int(line[4:]) for line in text.splitlines() if line.startswith('id: ')]


def test_i03_event_stream_resumes_after_reconnect_without_loss_or_repeats(tmp_path):
    app, client = session(tmp_path)
    store = app.state.store
    project, source = project_and_source(client)
    job = client.post(f'/v1/projects/{project}/jobs', json={'kind': 'probe', 'source_id': source}).json()['job_id']
    for index in range(5):
        store.event(job, 'stage.progress', {'stage': f's{index}'})
    store.finish(job, 'failed', error={'code': 'X', 'message': 'x'})
    everything = _events(client.get(f'/v1/jobs/{job}/events').text)
    assert everything == sorted(everything) and len(everything) == len(set(everything)) >= 6  # 單調、不重複
    resumed = _events(client.get(f'/v1/jobs/{job}/events', headers={'Last-Event-ID': str(everything[2])}).text)
    assert resumed == everything[3:]  # 從斷點續傳，不漏也不重複


def test_i03_running_jobs_send_a_heartbeat_at_least_every_two_seconds(tmp_path):
    app, client = session(tmp_path)
    store = app.state.store
    project, source = project_and_source(client)
    job = client.post(f'/v1/projects/{project}/jobs', json={'kind': 'probe', 'source_id': source}).json()['job_id']
    store.claim('cpu')
    threading.Timer(2.5, lambda: store.finish(job, 'succeeded', result={})).start()
    started = time.monotonic()
    text = client.get(f'/v1/jobs/{job}/events').text
    elapsed = time.monotonic() - started
    assert text.count(': heartbeat') >= int(elapsed // 2) >= 1


# ---- I04：重啟 ----

def test_i04_restart_interrupts_running_jobs_publishes_nothing_and_never_serves_staging(tmp_path):
    from app.studio.coordinator import Coordinator
    store = Store(tmp_path / 'state')
    project = store.create('project', {'name': '重啟'})['id']
    job = store.submit(project, {'kind': 'probe', 'source_id': 's'}, 'cpu')
    assert store.claim('cpu')['id'] == job['id']
    (store.root / 'artifacts').mkdir(exist_ok=True)
    (store.root / 'artifacts' / 'artifact_leftover.srt.staging').write_text('半份', encoding='utf-8')
    coordinator = Coordinator(store)
    coordinator.start()
    coordinator.close()
    after = store.job(job['id'])
    assert after['status'] == 'interrupted' and after['error']['code'] == 'SERVICE_RESTARTED' and after['result'] is None
    assert store.list('artifact', project)['items'] == []  # 重啟不發布任何成果
    _, client = session(tmp_path)
    assert client.get('/v1/artifacts/artifact_leftover/content').status_code == 404  # 暫存檔不能下載
    retried = client.post(f"/v1/jobs/{job['id']}/retry")
    assert retried.status_code == 202 and retried.json()['attempt'] == 2 and retried.json()['status'] == 'queued'


@pytest.mark.skipif(os.name != 'nt', reason='Windows 程序容器（kill-on-close）行為')
def test_i04_worker_processes_end_together_with_the_service(tmp_path):
    import psutil
    code = ("import os, sys; from pathlib import Path; from app.studio.process import ManagedProcess; "
            f"p = ManagedProcess([sys.executable, '-c', 'import time; time.sleep(60)'], r'{tmp_path}', Path(r'{tmp_path / 'w.log'}')); "
            "print(p.pid, flush=True); os._exit(0)")  # 服務程序直接結束（不做任何清理）
    child = int(subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=60,
                               env={**os.environ, 'PYTHONPATH': str(SRC)}).stdout.split()[0])
    deadline = time.monotonic() + 5
    while psutil.pid_exists(child) and time.monotonic() < deadline:
        time.sleep(.1)
    assert not psutil.pid_exists(child)  # 受管理的工作程序跟著結束，重啟後不會有孤兒程序占住顯示卡


# ---- I05：Range ----

def test_i05_ranges_return_exact_bytes_and_headers_for_assets_and_artifacts(tmp_path):
    app, client = session(tmp_path)
    store = app.state.store
    project, source = project_and_source(client)
    data = bytes(range(256)) * 4  # 1024 bytes
    media = tmp_path / 'media.wav'
    media.write_bytes(data)
    asset = store.create('asset', {'source_id': source, 'kind': 'audio', 'path': str(media)}, project)['id']
    job = client.post(f'/v1/projects/{project}/jobs', json={'kind': 'probe', 'source_id': source}).json()['job_id']
    artifact = store.publish(project, job, 'srt', data, 'srt')['id']
    for url in (f'/v1/assets/{asset}/content', f'/v1/artifacts/{artifact}/content'):
        for header, start, end in (('bytes=10-19', 10, 19), ('bytes=-5', 1019, 1023), ('bytes=1000-', 1000, 1023)):
            response = client.get(url, headers={'Range': header})
            assert response.status_code == 206, (url, header)
            assert response.headers['content-range'] == f'bytes {start}-{end}/1024'
            assert response.content == data[start:end + 1]
        beyond = client.get(url, headers={'Range': 'bytes=2000-'})
        assert beyond.status_code == 416 and beyond.headers['content-range'] == 'bytes */1024'
        assert client.get(url).content == data


def test_i05_library_never_lists_outside_the_allowed_folder(tmp_path):
    allowed = tmp_path / 'allowed'
    (allowed / 'sub').mkdir(parents=True)
    (tmp_path / 'secret.txt').write_text('x')
    app, client = session(tmp_path)
    app.state.config['roots'] = {'lib': str(allowed)}
    assert client.get('/v1/library', params={'root_id': 'lib'}).json()['items'][0]['name'] == 'sub'
    for relative in ('..', '../', 'sub/../..', str(tmp_path)):
        assert client.get('/v1/library', params={'root_id': 'lib', 'relative_path': relative}).status_code == 403
    assert client.get('/v1/library', params={'root_id': 'other'}).status_code == 403


# ---- I06：成果檔遺失或被改、快取重抓 ----

def test_i06_missing_or_changed_artifacts_are_refused_and_changed_cache_is_refetched(tmp_path, monkeypatch):
    app, client = session(tmp_path)
    store = app.state.store
    project, source = project_and_source(client)
    job = client.post(f'/v1/projects/{project}/jobs', json={'kind': 'probe', 'source_id': source}).json()['job_id']
    artifact = store.publish(project, job, 'srt', '字幕'.encode(), 'srt')
    path = store.artifact_path(artifact['id'])
    path.write_text('被改過', encoding='utf-8')
    changed = client.get(f"/v1/artifacts/{artifact['id']}/content")
    assert changed.status_code == 409 and changed.json()['error']['code'] == 'ARTIFACT_CORRUPT'
    path.unlink()
    missing = client.get(f"/v1/artifacts/{artifact['id']}/content")
    assert missing.status_code == 409 and missing.json()['error']['code'] == 'ARTIFACT_MISSING'
    # 下載快取：檔案內容和紀錄的 SHA 不符 → 不採用快取，改重新取得
    import hashlib
    from app.studio import media
    from app.studio.worker import Worker
    local = tmp_path / 'input.wav'
    local.write_bytes(b'original')
    local_source = store.create('source', {'kind': 'local', 'path': str(local), 'title': 'input.wav'}, project)
    store.revise(f"source:{local_source['id']}", 'source_metadata', {'duration_us': 2000000}, None, project, initial=None)
    cached = tmp_path / 'cached.m4a'
    cached.write_bytes(b'cached-audio')
    span = {'start_us': 0, 'end_us': 2000000}
    settings = {'format_policy': {}, 'boundary_policy': 'accurate', 'quality': 'source'}
    store.create('asset', {'source_id': local_source['id'], 'kind': 'audio', 'requested_range': span, 'acquisition_settings': settings,
                           'path': str(cached), 'content_hash': hashlib.sha256(b'something else').hexdigest(), 'audio_track_id': 'default'}, project)

    class Refetched(Exception):
        pass

    def refetch(*args, **kwargs):
        raise Refetched()
    monkeypatch.setattr(media, 'cut_media', refetch)
    acquire = store.submit(project, {'kind': 'acquire', 'source_id': local_source['id'], 'ranges': [span], 'boundary_policy': 'accurate'})
    with pytest.raises(Refetched):
        Worker(store, acquire).acquire()
    assert cached.read_bytes() == b'cached-audio'  # 舊檔不動


# ---- I07：授權、路徑 ----

def test_i07_wrong_token_is_refused_by_api_and_cli(tmp_path, monkeypatch, capsys):
    from app.studio.cli import main
    app = create_app(tmp_path / 'state', start_workers=False)
    with TestClient(app) as raw:
        denied = raw.get('/v1/projects', headers={'Authorization': 'Bearer wrong-token'})
        assert denied.status_code == 401 and denied.json()['error']['code'] == 'ACCESS_DENIED'
        monkeypatch.setenv('STUDIO_API_TOKEN', 'wrong-token')
        assert main(['project', 'list', '--json'], client=raw) == 2
        assert json.loads(capsys.readouterr().out)['error']['code'] == 'ACCESS_DENIED'


def test_i07_responses_do_not_expose_internal_absolute_paths_or_shell_routes(tmp_path):
    app, client = session(tmp_path)
    store = app.state.store
    project = client.post('/v1/projects', json={'name': '路徑'}).json()['project_id']
    uploaded = client.post(f'/v1/projects/{project}/uploads', files={'file': ('a.wav', b'audio', 'audio/wav')}).json()
    # JSON 會把 Windows 路徑的反斜線變成兩個：用 JSON 轉義後的樣子比對，否則永遠比不到（假通過）
    internal = json.dumps(str(tmp_path))[1:-1]
    assert internal in json.dumps({'probe': str(tmp_path / 'x')})  # 確認比對方式本身有效
    assert internal not in json.dumps(uploaded) and internal not in client.get(f'/v1/projects/{project}/sources').text
    asset = store.create('asset', {'source_id': uploaded['source_id'], 'kind': 'audio', 'path': str(tmp_path / 'x.wav')}, project)['id']
    assert internal not in client.get(f'/v1/assets/{asset}').text
    # 內部子工作（例如音樂分類）帶有資料目錄內的暫存路徑：API 不回傳
    child = store.submit(project, {'kind': 'classify', 'source_id': uploaded['source_id'], 'asset_id': asset,
                                   'pcm_path': str(tmp_path / 'state' / 'cache' / 'classify' / 'a.wav'), 'timeout_sec': 600}, 'cpu')
    assert internal not in client.get(f"/v1/jobs/{child['id']}").text
    assert internal not in client.get(f'/v1/projects/{project}/jobs').text
    paths = client.get('/openapi.json').json()['paths']
    assert not [p for p in paths if any(word in p.lower() for word in ('shell', 'exec', 'command', 'eval'))]


# ---- I08：CLI 與網頁共用 ----

def test_i08_cli_create_web_edit_cli_export_api_query_share_one_project(tmp_path, monkeypatch, capsys):
    from app.studio.cli import main
    app = create_app(tmp_path / 'state', start_workers=False)
    monkeypatch.setenv('STUDIO_DATA_DIR', str(tmp_path / 'state'))
    monkeypatch.delenv('STUDIO_API_TOKEN', raising=False)
    with TestClient(app) as api:
        assert main(['project', 'create', '--name', '共用', '--json'], client=api) == 0
        project = json.loads(capsys.readouterr().out)['id']
        assert main(['source', 'add', '--project', project, '--url', 'https://www.youtube.com/watch?v=Yn2mE6_tMC8'], client=api) == 0
        source = json.loads(capsys.readouterr().out)['id']
        api.get('/v1/session')  # 網頁：同一個服務、用工作階段修改剪輯清單
        edited = api.put(f'/v1/projects/{project}/sequence', json={'base_revision': 'seq_00', 'source_id': source,
                         'items': [{'id': 'one', 'kind': 'clip', 'start_us': 0, 'end_us': 1000000, 'name': '網頁加的'}]}).json()
        assert main(['export', '--project', project, '--sequence', edited['revision'], '--formats', 'mp4', '--wait', '--wait-timeout', '1', '--json'], client=api) == 5
        captured = capsys.readouterr()
        [line] = [l for l in captured.out.splitlines() if l.strip()]
        result = json.loads(line)  # stdout 只有一份 JSON
        job = api.get('/v1/jobs/' + (result.get('job_id') or result.get('id'))).json()
        assert job['project_id'] == project and job['body']['sequence_revision'] == edited['revision']
        assert all(json.loads(l) for l in captured.err.splitlines() if l.strip())  # 進度在 stderr


# ---- I09：取消、重試、doctor ----

def test_i09_retry_rules_and_cli_cancel_and_retry(tmp_path, monkeypatch, capsys):
    from app.studio.cli import main
    app = create_app(tmp_path / 'state', start_workers=False)
    monkeypatch.setenv('STUDIO_DATA_DIR', str(tmp_path / 'state'))
    monkeypatch.delenv('STUDIO_API_TOKEN', raising=False)
    with TestClient(app) as api:
        api.get('/v1/session')
        project, source = project_and_source(api)
        job = api.post(f'/v1/projects/{project}/jobs', json={'kind': 'probe', 'source_id': source}).json()['job_id']
        early = api.post(f'/v1/jobs/{job}/retry')
        assert early.status_code == 409 and early.json()['error']['code'] == 'JOB_NOT_RETRYABLE'
        assert main(['job', 'cancel', job, '--json'], client=api) == 0
        assert json.loads(capsys.readouterr().out)['status'] == 'cancelled'
        assert main(['job', 'retry', job, '--json'], client=api) == 0
        again = json.loads(capsys.readouterr().out)
        assert again['id'] != job and again['attempt'] == 2 and again['body'] == api.get(f'/v1/jobs/{job}').json()['body']


def test_i09_doctor_env_still_reports_the_environment_when_the_service_is_down(capsys, monkeypatch):
    from app.studio import cli
    monkeypatch.setenv('STUDIO_API_TOKEN', 'test-token')
    monkeypatch.setattr(cli, 'environment_report', lambda: {'items': {'ffmpeg': {'status': 'missing'}}}, raising=False)

    def down(request):
        raise httpx.ConnectError('refused', request=request)
    with httpx.Client(transport=httpx.MockTransport(down)) as unreachable:
        assert cli.main(['doctor', '--json'], client=unreachable) == 5  # 沒有 --env：照舊回服務不可達
        capsys.readouterr()
        assert cli.main(['doctor', '--env', '--json'], client=unreachable) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['api'] == 'unreachable' and report['environment']['items']['ffmpeg']['status'] == 'missing'


# ---- I11：計畫快照與預設組 ----

def test_i11_plan_snapshot_stays_frozen_presets_do_not_leak_and_pinned_sequence_is_checked(tmp_path):
    app, client = session(tmp_path)
    project, source = project_and_source(client)
    preset = client.post('/v1/presets', json={'name': '歌回', 'settings': {'analysis': {'mode': 'draft'}}})
    assert preset.status_code == 201
    assert client.post('/v1/presets', json={'name': '壞', 'settings': {'api_key': 'sk-x'}}).status_code == 422
    sequence = client.put(f'/v1/projects/{project}/sequence', json={'base_revision': 'seq_00', 'source_id': source,
                          'items': [{'id': 'one', 'kind': 'clip', 'start_us': 0, 'end_us': 1000000}]}).json()
    body = {'source_id': source, 'sequence_revision': sequence['revision'], 'analysis': dict(preset.json()['settings']['analysis']),
            'subtitles': {'policy': 'compare'}, 'deliverables': {'formats': ['srt']}}
    plan = client.post(f'/v1/projects/{project}/plans', json=body).json()
    graph = plan['dependency_graph']
    assert graph[0] == 'audio.acquire' and graph[-1] == 'selected.results -> export'
    assert graph.index('source_subtitles.acquire') < graph.index('analyze + source_subtitles -> compare')
    client.post('/v1/presets', json={'name': '歌回', 'settings': {'analysis': {'mode': 'quality'}}})  # 改預設組（新版本）
    assert client.get(f"/v1/plans/{plan['plan_id']}").json() == plan  # 舊計畫快照不變
    client.put(f'/v1/projects/{project}/sequence', json={'base_revision': sequence['revision'], 'source_id': source,
               'items': [{'id': 'two', 'kind': 'clip', 'start_us': 0, 'end_us': 2000000}]})
    stale = client.post(f"/v1/plans/{plan['plan_id']}/run")
    assert stale.status_code == 409 and stale.json()['error']['code'] == 'REVISION_CONFLICT'  # 釘住的剪輯版本已變


# ---- I12：不支援的要求要明說 ----

def test_i12_unsupported_requests_are_explicit(tmp_path):
    app, client = session(tmp_path)
    project = client.post('/v1/projects', json={'name': '不支援'}).json()['project_id']
    local = client.post(f'/v1/projects/{project}/uploads', files={'file': ('a.wav', b'audio', 'audio/wav')}).json()['source_id']
    captions = client.post(f'/v1/projects/{project}/plans', json={'source_id': local, 'subtitles': {'policy': 'source'}})
    assert captions.status_code == 422 and captions.json()['error']['code'] == 'SOURCE_CAPTIONS_UNAVAILABLE'
    jobs = f'/v1/projects/{project}/jobs'
    for bad in ({'kind': 'export', 'source_id': local, 'ranges': [{'start_us': 0, 'end_us': 1}], 'cut_mode': 'smart'},
                {'kind': 'acquire', 'source_id': local, 'quality': 'ultra'},
                {'kind': 'analyze', 'source_id': local, 'profile': 'best'},
                {'kind': 'analyze', 'source_id': local, 'engine': 'another-engine'}):
        response = client.post(jobs, json=bad)
        assert response.status_code == 422 and response.json()['error']['code'] == 'INVALID_REQUEST', bad


def test_malformed_json_body_is_a_clean_error_not_a_500(tmp_path):
    """M6-2 真跑發現：body 不是合法 JSON 時回 500 Internal Server Error（沒有錯誤碼、看不懂）。
    應該回 400/422 與穩定錯誤碼，訊息講中文。"""
    _, client = session(tmp_path)
    project = client.post('/v1/projects', json={'name': '壞 JSON'}).json()['project_id']
    broken = client.post(f'/v1/projects/{project}/sources/local-file',
                         content=chr(123) + chr(34) + chr(112) + chr(97) + chr(116) + chr(104) + chr(34) + chr(58) + chr(34) + chr(68) + chr(58) + chr(92) + chr(115) + chr(34) + chr(125),  # 反斜線沒跳脫的 JSON
                         headers={'Content-Type': 'application/json'})
    assert broken.status_code in (400, 422)
    body = broken.json()
    assert body['error']['code'] == 'INVALID_JSON'
    assert 'JSON' in body['error']['message']
    # 其他吃 request.json() 的端點也一樣
    other = client.post('/v1/maintenance/cleanup', content='{', headers={'Content-Type': 'application/json'})
    assert other.status_code in (400, 422) and other.json()['error']['code'] == 'INVALID_JSON'
