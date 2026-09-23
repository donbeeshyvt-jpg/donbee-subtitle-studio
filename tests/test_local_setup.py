"""本機方案：全部以隔離路徑與替身驗證，不安裝套件、不下載權重。"""
import json
from pathlib import Path
import pytest
import os

from bootstrap import run


@pytest.fixture(autouse=True)
def isolate_model_paths(monkeypatch):
    for name in ('HF_HUB_CACHE', 'TORCH_HOME', 'STUDIO_MODELS_DIR'):
        monkeypatch.delenv(name, raising=False)


def test_local_requires_confirmation_before_install(monkeypatch, tmp_path):
    monkeypatch.setattr(run, 'collect_state', lambda *a: pytest.fail('未確認不得開始安裝檢查'))
    assert run.main(['--root', str(tmp_path), '--local']) == 2


def test_local_model_plan_has_large_v3_first_and_all_required(tmp_path):
    from bootstrap.local_setup import local_plan
    from app.studio import model_store
    manifest = model_store.load_manifest()
    plan = local_plan(manifest, tmp_path / 'models')
    assert plan['items'][0]['id'] == 'hf:Systran/faster-whisper-large-v3'
    assert all(item['required'] for item in plan['items'])
    assert len(plan['items']) == 6
    assert all(Path(item['target']).is_relative_to(tmp_path / 'models') for item in plan['items'] if item.get('target'))


def test_local_prepare_does_not_download_if_whisperx_import_fails(monkeypatch, tmp_path):
    from bootstrap import local_setup
    monkeypatch.delenv('HF_HUB_CACHE', raising=False)
    monkeypatch.delenv('TORCH_HOME', raising=False)
    monkeypatch.setenv('STUDIO_MODELS_DIR', str(tmp_path / 'models'))
    monkeypatch.setattr(local_setup, 'check_runtime', lambda: (_ for _ in ()).throw(RuntimeError('IMPORT_FAILED')))
    monkeypatch.setattr(local_setup, 'prepare_models', lambda *a: pytest.fail('WhisperX 未就緒不得下載模型'))
    assert local_setup.main(['--models-dir', str(tmp_path / 'models'), '--confirm']) == 1


def test_local_prepare_failure_blocks_serve(monkeypatch, tmp_path):
    import socket
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **kw: (_ for _ in ()).throw(OSError()))
    (tmp_path / 'requirements.lock').write_text('', encoding='utf-8')
    state = dict(env={}, python_ok=True, venv_ok=True, venv_python='python.exe', installed={},
                 tools_missing=[], models_missing=[], gpu_available=False, groups=('core', 'models'))
    monkeypatch.setattr(run, 'collect_state', lambda *a: state)
    monkeypatch.setattr(run, 'execute', lambda *a, **kw: [])
    monkeypatch.setattr(run, 'prepare_local', lambda *a: 1)
    monkeypatch.setattr(run.setup_support, 'prepare_downloader', lambda *a: None)
    monkeypatch.setattr(run, 'serve', lambda *a: pytest.fail('安裝失敗不可啟動'))
    assert run.main(['--root', str(tmp_path), '--local', '--confirm', '--serve']) == 1


def test_local_check_only_never_installs(monkeypatch, tmp_path):
    (tmp_path / 'requirements.lock').write_text('', encoding='utf-8')
    state = dict(env={}, python_ok=True, venv_ok=False, venv_python='python.exe', installed={},
                 tools_missing=['ffmpeg'], models_missing=['hf:test'], gpu_available=False, groups=('core', 'models'))
    monkeypatch.setattr(run, 'collect_state', lambda *a: state)
    monkeypatch.setattr(run, 'prepare_local', lambda *a: pytest.fail('只檢查不得下載'))
    monkeypatch.setattr(run.venv, 'ensure_venv', lambda *a: pytest.fail('只檢查不得建立環境'))
    assert run.main(['--root', str(tmp_path), '--local', '--check-only', '--json']) == 0
    assert json.loads((tmp_path / 'data/bootstrap-last.json').read_text(encoding='utf-8'))['models_missing'] == ['hf:test']


def test_missing_system_tools_blocks_installs(monkeypatch, tmp_path):
    (tmp_path / 'requirements.lock').write_text('', encoding='utf-8')
    state = dict(env={}, python_ok=True, venv_ok=False, venv_python='python.exe', installed={},
                 tools_missing=['ffmpeg'], models_missing=[], gpu_available=False, groups=('core', 'models'))
    monkeypatch.setattr(run, 'collect_state', lambda *a: state)
    monkeypatch.setattr(run, 'execute', lambda *a, **kw: pytest.fail('缺系統工具先給指引，不先下載大型套件'))
    assert run.main(['--root', str(tmp_path), '--local', '--confirm', '--serve']) == 2


def test_interactive_decline_never_installs(monkeypatch, tmp_path):
    import sys
    (tmp_path / 'requirements.lock').write_text('', encoding='utf-8')
    state = dict(env={}, python_ok=True, venv_ok=False, venv_python='python.exe', installed={},
                 tools_missing=[], models_missing=[], gpu_available=False, groups=('core', 'models'))
    monkeypatch.setattr(run, 'collect_state', lambda *a: state)
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
    answers = iter(['1', 'n'])
    monkeypatch.setattr('builtins.input', lambda *a: next(answers))
    monkeypatch.setattr(run.setup_support, 'estimate_models', lambda *a: [])
    monkeypatch.setattr(run, 'execute', lambda *a, **kw: pytest.fail('拒絕後不得安裝'))
    assert run.main(['--root', str(tmp_path), '--interactive']) == 2


def test_local_flow_rechecks_before_serve(monkeypatch, tmp_path):
    import socket
    (tmp_path / 'requirements.lock').write_text('', encoding='utf-8')
    state = dict(env={}, python_ok=True, venv_ok=True, venv_python='python.exe', installed={},
                 tools_missing=[], models_missing=[], gpu_available=False, groups=('core', 'models'))
    calls = []
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **kw: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(run, 'collect_state', lambda *a: (calls.append('check') or state))
    monkeypatch.setattr(run, 'execute', lambda *a, **kw: (calls.append('packages') or []))
    monkeypatch.setattr(run.setup_support, 'prepare_downloader', lambda *a: calls.append('downloader'))
    monkeypatch.setattr(run, 'prepare_local', lambda *a: (calls.append('models') or 0))
    monkeypatch.setattr(run, 'serve', lambda *a: (calls.append('serve') or 0))
    assert run.main(['--root', str(tmp_path), '--local', '--confirm', '--serve']) == 0
    assert calls == ['check', 'packages', 'downloader', 'check', 'models', 'check', 'serve']


def test_core_snapshot_with_only_config_is_not_installed(tmp_path):
    from app.studio import model_store
    from bootstrap.models import missing_models
    manifest = {'version': 1, 'hf': [{'repo': 'test/core', 'required': True, 'required_files': ['config.json', 'model.bin', 'tokenizer.json']}]}
    folder = tmp_path / 'hf/models--test--core/snapshots/rev'
    folder.mkdir(parents=True)
    (folder / 'config.json').write_text('{}')
    assert model_store.check_models(manifest, tmp_path)['required_missing'] == ['hf:test/core']
    assert missing_models(manifest, tmp_path) == ['hf:test/core']


def test_local_cache_conflict_stops_before_runtime(monkeypatch, tmp_path):
    from bootstrap import local_setup
    monkeypatch.setenv('HF_HUB_CACHE', str(tmp_path / 'elsewhere'))
    monkeypatch.setattr(local_setup, 'check_runtime', lambda: pytest.fail('快取位置衝突時先停止'))
    assert local_setup.main(['--models-dir', str(tmp_path / 'models'), '--confirm']) == 2


def test_whisperx_plan_download_uses_injected_io_and_rechecks(monkeypatch, tmp_path):
    from bootstrap import local_setup
    from app.studio import model_store
    manifest = {'version': 1, 'hf': [{'repo': 'Systran/faster-whisper-large-v3', 'required': True, 'required_files': ['model.bin']}]}
    monkeypatch.setattr(model_store, 'load_manifest', lambda: manifest)
    monkeypatch.setenv('STUDIO_IMPORT_SOURCE_HF', str(tmp_path / 'no-source'))
    calls = []
    def fake_download(repo, cache_dir, revision=None, **kwargs):
        calls.append(repo)
        folder = Path(cache_dir) / model_store.repo_folder(repo) / 'snapshots/test'
        folder.mkdir(parents=True)
        (folder / 'model.bin').write_bytes(b'test')
    monkeypatch.setattr(model_store, '_default_hf_download', fake_download)
    assert local_setup.prepare_models(tmp_path / 'models')['offline_ready'] is True
    assert calls == ['Systran/faster-whisper-large-v3']


def test_hf_download_forwards_file_progress(monkeypatch, tmp_path):
    import huggingface_hub
    from app.studio import model_store
    events = []
    def fake_snapshot(**kwargs):
        with kwargs['tqdm_class'](total=2, mininterval=0, miniters=1) as bar:
            bar.update(1)
            bar.update(1)
    monkeypatch.setattr(huggingface_hub, 'snapshot_download', fake_snapshot)
    model_store._default_hf_download('test/model', tmp_path, progress=events.append)
    assert any(event['files_done'] == 1 and event['files_total'] == 2 for event in events)


def test_incomplete_download_is_failed_not_success(tmp_path):
    from app.studio import model_store
    manifest = {'version': 1, 'hf': [{'repo': 'test/core', 'required_files': ['config.json', 'model.bin']}]}
    plan = model_store.plan_download(manifest, tmp_path, source_hf=tmp_path / 'empty')
    def partial(repo, cache_dir, revision=None):
        folder = Path(cache_dir) / model_store.repo_folder(repo) / 'snapshots/test'
        folder.mkdir(parents=True)
        (folder / 'config.json').write_text('{}')
    assert model_store.run_download(plan, hf_download=partial)['items'][0]['status'] == 'failed'


def test_browser_opens_only_after_health_and_page(monkeypatch):
    import httpx
    import threading
    from app.studio import cli
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path == '/v1/health':
            return httpx.Response(200, json={'status': 'ok', 'name': '冬比字幕工作室'})
        return httpx.Response(200, text='<html></html>', headers={'content-type': 'text/html'})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(cli.httpx, 'Client', lambda **kw: client)
    monkeypatch.setattr(cli.webbrowser, 'open', lambda url: calls.append(url))
    assert cli._open_when_ready('http://127.0.0.1:8765', threading.Event()) is True
    assert calls == ['/v1/health', '/v2/', 'http://127.0.0.1:8765/v2/']


def test_browser_does_not_open_when_page_missing(monkeypatch):
    import httpx
    import threading
    from app.studio import cli
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    monkeypatch.setattr(cli.httpx, 'Client', lambda **kw: client)
    monkeypatch.setattr(cli.webbrowser, 'open', lambda url: pytest.fail('頁面不可用不得開瀏覽器'))
    assert cli._open_when_ready('http://127.0.0.1:8765', threading.Event(), timeout=.01) is False


@pytest.mark.skipif(os.name != 'nt', reason='Windows winget 安裝流程')
def test_system_install_uses_allowlist_and_deduplicates_ffmpeg(monkeypatch):
    from bootstrap import setup_support
    monkeypatch.setenv('PATH', os.environ.get('PATH', ''))
    monkeypatch.setattr(setup_support.shutil, 'which', lambda name: 'winget')
    calls = []
    monkeypatch.setattr(setup_support, 'run_visible', lambda argv, **kw: calls.append(argv))
    setup_support.install_tools(['ffmpeg', 'ffprobe', 'node'])
    assert len(calls) == 2
    assert calls[0][3] == 'Gyan.FFmpeg' and calls[1][3] == 'OpenJS.NodeJS.LTS'
    assert all('--exact' in argv and '--source' in argv for argv in calls)


def test_downloader_is_installed_only_in_project_environment(monkeypatch, tmp_path):
    from bootstrap import setup_support
    from types import SimpleNamespace
    monkeypatch.delenv('STUDIO_YTDLP_PYTHON', raising=False)
    calls = []
    monkeypatch.setattr(setup_support, 'run_visible', lambda argv, **kw: calls.append(argv))
    monkeypatch.setattr(setup_support.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0))
    setup_support.prepare_downloader(tmp_path)
    assert calls[0][-1] == str(tmp_path / '.venv-download')
    assert Path(calls[1][0]).is_relative_to(tmp_path / '.venv-download')
    assert calls[1][-1] == 'yt-dlp[default]'


def test_custom_downloader_is_never_modified(monkeypatch, tmp_path):
    from bootstrap import setup_support
    monkeypatch.setenv('STUDIO_YTDLP_PYTHON', str(tmp_path / 'custom-python'))
    monkeypatch.setattr(setup_support, 'run_visible', lambda *a, **kw: pytest.fail('不可安裝進自訂環境'))
    with pytest.raises(RuntimeError, match='CUSTOM_DOWNLOADER'):
        setup_support.prepare_downloader(tmp_path)


def test_estimate_failure_keeps_unknown_instead_of_zero(monkeypatch, tmp_path):
    from bootstrap import setup_support
    (tmp_path / 'models.manifest.json').write_text(json.dumps({'hf': [{'repo': 'test/model'}]}))
    monkeypatch.setattr(setup_support.urllib.request, 'urlopen', lambda *a, **kw: (_ for _ in ()).throw(OSError()))
    assert setup_support.estimate_models(tmp_path, ['hf:test/model']) == [{'id': 'hf:test/model', 'download_bytes': None}]
