"""M0-7 供應者設定與連線測試（P-01、P-07、P-08 與 API 契約）。HTTP 供應者為本機替身，不是真模型品質驗收。"""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading

import pytest
from fastapi.testclient import TestClient

from app.studio import provider_secrets, providers
from app.studio.api import create_app
from app.studio.providers import ProviderError


@contextmanager
def fake_provider(routes, calls=None):
    """依路徑回應的本機 HTTP 替身；routes: {(method, path): (status, payload)}。"""
    calls = [] if calls is None else calls

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _serve(self, method):
            path = self.path.split('?')[0]
            length = int(self.headers.get('Content-Length') or 0)
            body = json.loads(self.rfile.read(length)) if length else None
            calls.append((method, path, body, self.headers.get('Authorization')))
            status, payload = routes.get((method, path), (404, {'error': 'not found'}))
            data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._serve('GET')

        def do_POST(self):
            self._serve('POST')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_address[1]}/v1', calls
    finally:
        server.shutdown()
        server.server_close()


def _free_port():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _chat(content):
    return {'choices': [{'message': {'content': content}}], 'usage': {'prompt_tokens': 3, 'completion_tokens': 1}}


def local_config(url, **extra):
    base = dict(id='local-test', adapter='openai_compatible', base_url=url, model='test-model', gpu_ownership='cpu',
                timeout_sec=5, max_retries=0, response_format_mode='json_schema')
    base.update(extra)
    return base


# ---- P-07 金鑰儲存 ----

def test_secret_store_roundtrip_and_never_touches_config(tmp_path):
    assert provider_secrets.secret_configured(tmp_path, 'api-deepseek') is False
    provider_secrets.set_secret(tmp_path, 'api-deepseek', 'sk-test-secret-value')
    assert provider_secrets.secret_configured(tmp_path, 'api-deepseek') is True
    assert provider_secrets.get_secret(tmp_path, 'api-deepseek') == 'sk-test-secret-value'
    stored = tmp_path / 'secrets.json'
    assert stored.is_file()
    assert not (tmp_path / 'config.json').exists()
    provider_secrets.delete_secret(tmp_path, 'api-deepseek')
    assert provider_secrets.secret_configured(tmp_path, 'api-deepseek') is False
    assert 'sk-test-secret-value' not in stored.read_text(encoding='utf-8')


def test_secret_store_rejects_bad_values(tmp_path):
    for value in ['', '   ', 'a' * 5000, 'with\nnewline']:
        with pytest.raises(ProviderError):
            provider_secrets.set_secret(tmp_path, 'api-deepseek', value)
    with pytest.raises(ProviderError):
        provider_secrets.set_secret(tmp_path, '../escape', 'sk-x')


def test_resolve_secret_prefers_env_then_store(monkeypatch, tmp_path):
    config = dict(id='api-deepseek', api_key_env='DEEPSEEK_API_KEY')
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    assert provider_secrets.resolve_secret(config, tmp_path) is None
    provider_secrets.set_secret(tmp_path, 'api-deepseek', 'from-store')
    assert provider_secrets.resolve_secret(config, tmp_path) == 'from-store'
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'from-env')
    assert provider_secrets.resolve_secret(config, tmp_path) == 'from-env'


def test_settings_use_store_secret_and_reject_inline_key(monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    remote = dict(id='api-deepseek', base_url='https://api.deepseek.com', model='deepseek-chat',
                  api_key_env='DEEPSEEK_API_KEY', allow_remote=True)
    with pytest.raises(ProviderError) as missing:
        providers._settings(remote)
    assert missing.value.args[0] == 'PROVIDER_SECRET_MISSING'
    settings = providers._settings(remote, secret='sk-from-store')
    assert settings['headers']['Authorization'] == 'Bearer sk-from-store'
    with pytest.raises(ProviderError) as inline:
        providers._settings(dict(remote, api_key='sk-inline'))
    assert inline.value.args[0] == 'USE_API_KEY_ENV'


# ---- P-08 探測狀態矩陣 ----

def test_probe_status_service_unreachable():
    config = local_config(f'http://127.0.0.1:{_free_port()}/v1')
    result = providers.probe_status(config)
    assert result['status'] == 'service_unreachable'
    assert result['elapsed_ms'] >= 0 and result['checked_at']


def test_probe_status_auth_and_rate_limit():
    with fake_provider({('GET', '/v1/models'): (401, {'error': 'bad key'})}) as (url, _):
        assert providers.probe_status(local_config(url))['status'] == 'auth_failed'
    with fake_provider({('GET', '/v1/models'): (429, {'error': 'slow down'})}) as (url, _):
        assert providers.probe_status(local_config(url))['status'] == 'rate_limited'


def test_probe_status_model_not_loaded_without_triggering_chat():
    routes = {('GET', '/v1/models'): (200, {'data': [{'id': 'other-model'}]})}
    with fake_provider(routes) as (url, calls):
        result = providers.probe_status(local_config(url))
    assert result['status'] == 'model_not_loaded'
    assert result['model_available'] is False
    assert not any(method == 'POST' for method, *_ in calls)


def test_probe_status_lmstudio_not_loaded_state_skips_chat():
    routes = {('GET', '/v1/models'): (200, {'data': [{'id': 'test-model'}]}),
              ('GET', '/api/v0/models'): (200, {'data': [{'id': 'test-model', 'state': 'not-loaded'}]})}
    with fake_provider(routes) as (url, calls):
        result = providers.probe_status(local_config(url))
    assert result['status'] == 'model_not_loaded'
    assert result['detail'] == 'listed_but_not_loaded'
    assert not any(method == 'POST' for method, *_ in calls)


def test_probe_status_structured_output_unsupported_falls_back_to_text():
    routes = {('GET', '/v1/models'): (200, {'data': [{'id': 'test-model'}]}),
              ('POST', '/v1/chat/completions'): (200, _chat('not json'))}
    with fake_provider(routes) as (url, calls):
        result = providers.probe_status(local_config(url))
    assert result['status'] == 'structured_output_unsupported'
    assert result['text_ok'] is True


def test_probe_status_ready_with_json_schema():
    routes = {('GET', '/v1/models'): (200, {'data': [{'id': 'test-model'}]}),
              ('POST', '/v1/chat/completions'): (200, _chat('{"ok": true}'))}
    with fake_provider(routes) as (url, calls):
        result = providers.probe_status(local_config(url))
    assert result['status'] == 'ready'
    assert result['structured_output'] == 'verified_json_schema'
    assert result['model_available'] is True


def test_probe_status_remote_secret_missing_without_network(monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    remote = dict(id='api-deepseek', base_url='https://api.deepseek.com', model='deepseek-chat',
                  api_key_env='DEEPSEEK_API_KEY', allow_remote=True, timeout_sec=5)
    result = providers.probe_status(remote)
    assert result['status'] == 'secret_missing'


def test_probe_status_never_echoes_secret():
    with fake_provider({('GET', '/v1/models'): (401, {'error': 'bad key sk-should-not-leak'})}) as (url, _):
        config = local_config(url, allow_remote=True, api_key_env='UNSET_KEY_FOR_TEST')
        result = providers.probe_status(config, secret='sk-should-not-leak')
    assert result['status'] == 'auth_failed'
    assert 'sk-should-not-leak' not in json.dumps(result, ensure_ascii=False)


# ---- API 契約 ----

def client(tmp_path):
    app = create_app(tmp_path, start_workers=False)
    result = TestClient(app)
    assert result.get('/v1/session').status_code == 200
    return result


def test_default_providers_seeded_and_listed_without_secrets(tmp_path):
    c = client(tmp_path)
    items = c.get('/v1/providers').json()['items']
    ids = {item['id'] for item in items}
    # 2026-09-21 使用者：只留 LM Studio、llama.cpp、OpenRouter 三個來源；同日新增 ElevenLabs（只做轉錄）
    assert ids == {'local-lmstudio', 'local-llamacpp', 'api-openrouter', 'api-elevenlabs'}
    for item in items:
        assert set(['id', 'model', 'base_url', 'local', 'allow_remote', 'secret_configured', 'response_format_mode']) <= set(item)
        assert 'api_key' not in item and 'secret' not in item
    remote = next(item for item in items if item['id'] == 'api-openrouter')
    assert remote['local'] is False and remote['allow_remote'] is True and remote['secret_configured'] is False
    saved = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    assert len(saved['providers']) == 4 and saved['provider_defaults_added'] == ['api-elevenlabs']


def test_secret_endpoints_write_only(tmp_path):
    c = client(tmp_path)
    assert c.put('/v1/providers/api-openrouter/secret', json={'secret': 'sk-live-test'}).status_code == 200
    item = next(x for x in c.get('/v1/providers').json()['items'] if x['id'] == 'api-openrouter')
    assert item['secret_configured'] is True
    assert 'sk-live-test' not in (tmp_path / 'config.json').read_text(encoding='utf-8')
    assert 'sk-live-test' not in c.get('/v1/providers').text
    assert c.put('/v1/providers/unknown-id/secret', json={'secret': 'x'}).status_code == 404
    assert c.put('/v1/providers/api-openrouter/secret', json={'secret': ''}).status_code == 422
    assert c.delete('/v1/providers/api-openrouter/secret').status_code == 200
    item = next(x for x in c.get('/v1/providers').json()['items'] if x['id'] == 'api-openrouter')
    assert item['secret_configured'] is False


def test_provider_create_update_delete_with_validation(tmp_path):
    c = client(tmp_path)
    body = dict(id='local-custom', adapter='openai_compatible', base_url='http://127.0.0.1:9999/v1', model='m',
                gpu_ownership='cpu', timeout_sec=30, max_retries=1, response_format_mode='text')
    assert c.post('/v1/providers', json=body).status_code == 201
    assert c.post('/v1/providers', json=body).status_code == 409
    assert c.post('/v1/providers', json=dict(body, id='bad-key', api_key='inline')).status_code == 422
    assert c.post('/v1/providers', json=dict(body, id='remote-no-consent', base_url='https://example.com/v1')).status_code == 422
    assert c.put('/v1/providers/local-custom', json=dict(body, model='m2')).status_code == 200
    saved = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    assert next(p for p in saved['providers'] if p['id'] == 'local-custom')['model'] == 'm2'
    assert c.delete('/v1/providers/local-custom').status_code == 200
    assert 'local-custom' not in {p['id'] for p in c.get('/v1/providers').json()['items']}


def test_probe_endpoint_returns_status_and_records_last_probe(tmp_path):
    c = client(tmp_path)
    routes = {('GET', '/v1/models'): (200, {'data': [{'id': 'test-model'}]}),
              ('POST', '/v1/chat/completions'): (200, _chat('{"ok": true}'))}
    with fake_provider(routes) as (url, _):
        assert c.post('/v1/providers', json=local_config(url, id='local-fake')).status_code == 201
        probe = c.post('/v1/providers/local-fake/probe').json()
    assert probe['status'] == 'ready'
    item = next(x for x in c.get('/v1/providers').json()['items'] if x['id'] == 'local-fake')
    assert item['last_probe']['status'] == 'ready' and item['last_probe']['checked_at']
    unreachable = c.post('/v1/providers/local-llamacpp/probe').json()
    assert unreachable['status'] in {'service_unreachable', 'model_not_loaded', 'auth_failed', 'ready', 'structured_output_unsupported'}


def test_defaults_follow_official_docs_checked_2026_09_16():
    """官網核對：OpenRouter 用「組織/模型」並可 json_schema；llama.cpp 預設 8080。
    2026-09-21 使用者：只留三個來源（DeepSeek 直連移除），OpenRouter 語言模型改 deepseek/deepseek-v4.1-flash（清單與 json_schema 已用免費端點核對）。"""
    from app.studio.providers import DEFAULT_PROVIDERS, _settings
    by_id = {item['id']: item for item in DEFAULT_PROVIDERS}
    assert 'api-deepseek' not in by_id
    assert by_id['api-openrouter']['model'] == 'deepseek/deepseek-v4.1-flash' and by_id['api-openrouter']['response_format_mode'] == 'json_schema'
    assert by_id['local-llamacpp']['base_url'] == 'http://127.0.0.1:8080/v1'
    headers = _settings({**by_id['api-openrouter'], 'allow_remote': True}, secret='sk-or-test')['headers']
    assert headers['X-OpenRouter-Title'] == 'DonBee Subtitle Studio' and headers['X-Title'] == 'DonBee Subtitle Studio'
    assert headers['Authorization'] == 'Bearer sk-or-test'
    other = dict(id='custom', base_url='https://api.example.com', model='m', allow_remote=True)
    assert 'X-OpenRouter-Title' not in _settings(other, secret='sk-test')['headers']  # 只有 OpenRouter 帶應用識別標頭


def test_llama_server_guidance_mentions_alias_and_project_gguf_dir():
    from bootstrap.checks import GUIDANCE
    assert '--alias loaded-gguf' in GUIDANCE['llama_server'] and 'models/gguf' in GUIDANCE['llama_server'] and '--port 8080' in GUIDANCE['llama_server']


def test_probe_history_survives_restart_and_never_stores_secret(tmp_path, monkeypatch):
    import json as json_module
    from fastapi.testclient import TestClient
    from app.studio import api as api_module
    def fake_probe(provider, secret=None):
        return dict(status='ready', detail=None, checked_at='2026-09-16T10:00:00+00:00', elapsed_ms=12, model_available=True,
                    structured_output='verified_json_schema', secret_echo=secret)
    monkeypatch.setattr(api_module, 'probe_status', fake_probe)
    client = TestClient(api_module.create_app(tmp_path, start_workers=False))
    client.get('/v1/session')
    client.put('/v1/providers/api-openrouter/secret', json={'secret': 'unit-test-secret-placeholder'})
    assert client.post('/v1/providers/api-openrouter/probe').json()['status'] == 'ready'
    again = TestClient(api_module.create_app(tmp_path, start_workers=False))
    again.get('/v1/session')
    item = next(p for p in again.get('/v1/providers').json()['items'] if p['id'] == 'api-openrouter')
    assert item['last_probe']['status'] == 'ready' and item['last_probe']['checked_at'] == '2026-09-16T10:00:00+00:00'
    assert 'secret_echo' not in json_module.dumps(item)
    config_text = (tmp_path / 'config.json').read_text(encoding='utf-8')
    assert 'unit-test-secret-placeholder' not in config_text and 'secret_echo' not in config_text and '"last_probe"' in config_text


def test_legacy_seeded_default_models_are_upgraded_but_custom_entries_untouched(tmp_path):
    import json as json_module
    from fastapi.testclient import TestClient
    from app.studio import api as api_module
    common = dict(adapter='openai_compatible', allow_remote=True, gpu_ownership='cpu', timeout_sec=120, max_retries=1,
                  max_context_chars=24000, max_output_tokens=4096)
    legacy = [dict(id='api-deepseek', base_url='https://api.deepseek.com', model='deepseek-chat', api_key_env='DEEPSEEK_API_KEY', response_format_mode='json_object', **common),
              dict(id='api-openrouter', base_url='https://openrouter.ai/api/v1', model='deepseek/deepseek-chat', api_key_env='OPENROUTER_API_KEY', response_format_mode='json_schema', **common),
              dict(id='custom-deepseek', base_url='https://api.deepseek.com', model='deepseek-chat', api_key_env='DEEPSEEK_API_KEY', response_format_mode='json_object', **common)]
    TestClient(api_module.create_app(tmp_path, start_workers=False)).get('/v1/session')  # 先由程式寫出完整 config（含 token）
    seeded = json_module.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    seeded['providers'] = legacy
    (tmp_path / 'config.json').write_text(json_module.dumps(seeded), encoding='utf-8')
    client = TestClient(api_module.create_app(tmp_path, start_workers=False))
    client.get('/v1/session')
    models = {p['id']: p['model'] for p in client.get('/v1/providers').json()['items']}
    assert models == {'api-deepseek': 'deepseek-flash', 'api-openrouter': 'openai/gpt-4o-mini', 'custom-deepseek': 'deepseek-chat'}
    saved = json_module.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    assert [p['model'] for p in saved['providers']] == ['deepseek-flash', 'openai/gpt-4o-mini', 'deepseek-chat']


def test_local_default_is_users_gemma_with_reasoning_off_and_legacy_upgraded(tmp_path):
    import json as json_module
    from fastapi.testclient import TestClient
    from app.studio import api as api_module
    from app.studio.providers import DEFAULT_PROVIDERS
    local = next(p for p in DEFAULT_PROVIDERS if p['id'] == 'local-lmstudio')
    # 2026-09-20 使用者：「本地模型就依照當前載入的即可」→ 本機端口的 model 是 auto，送出前才問服務載入了哪一個
    assert local['model'] == 'auto' and local['reasoning_effort'] == 'none'
    TestClient(api_module.create_app(tmp_path, start_workers=False)).get('/v1/session')
    seeded = json_module.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    seeded['providers'] = [dict(id='local-lmstudio', adapter='openai_compatible', base_url='http://127.0.0.1:1234/v1', model='dongbi-qwen15-cpu',
                                gpu_ownership='cpu', timeout_sec=180, max_retries=1, max_context_chars=6000, max_output_tokens=1536, response_format_mode='json_schema')]
    (tmp_path / 'config.json').write_text(json_module.dumps(seeded), encoding='utf-8')
    client = TestClient(api_module.create_app(tmp_path, start_workers=False))
    client.get('/v1/session')
    item = next(p for p in client.get('/v1/providers').json()['items'] if p['id'] == 'local-lmstudio')
    assert item['model'] == 'auto' and item['reasoning_effort'] == 'none'
    saved = client.put('/v1/providers/local-lmstudio', json={**{k: v for k, v in item.items() if k in ('id', 'adapter', 'base_url', 'model', 'gpu_ownership', 'timeout_sec', 'max_retries', 'max_context_chars', 'max_output_tokens', 'response_format_mode')}, 'reasoning_effort': 'low'})
    assert saved.status_code in (200, 201), saved.text
    assert next(p for p in client.get('/v1/providers').json()['items'] if p['id'] == 'local-lmstudio')['reasoning_effort'] == 'low'


def test_openrouter_defaults_and_out_of_credit_are_explicit():
    """2026-09-21 使用者指定 OpenRouter 模型；真呼叫時帳戶餘額用完回 402，要講「餘額不足」而不是「供應者錯誤」。"""
    from app.studio import providers
    from app.studio.worker import TRANSPORT_FAILURES as PROVIDER_MESSAGES
    ids = [p['id'] for p in providers.DEFAULT_PROVIDERS]
    assert ids == ['local-lmstudio', 'local-llamacpp', 'api-openrouter', 'api-elevenlabs']  # 三個文字來源＋只做轉錄的 ElevenLabs
    remote = next(p for p in providers.DEFAULT_PROVIDERS if p['id'] == 'api-openrouter')
    assert remote['model'] == 'deepseek/deepseek-v4.1-flash'
    assert remote['transcription_model'] == 'meta/muse-voice-transcribe-1.0'
    assert remote['reasoning_effort'] == 'none'  # 思考關掉：校字不需要，也省錢
    assert providers._status_from_error('PROVIDER_CREDITS_EXHAUSTED') == 'credits_exhausted'
    assert '餘額' in PROVIDER_MESSAGES['PROVIDER_CREDITS_EXHAUSTED']
