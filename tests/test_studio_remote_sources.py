"""2026-09-21 使用者：「新增一個轉錄可以用的 API：ElevenLabs（跟別的轉錄設定一樣）；OpenRouter 再新增轉錄可以用的 microsoft/mai-transcribe-2」。

- ElevenLabs Speech to Text（官網 api-reference/speech-to-text/convert）：POST {base}/speech-to-text，multipart/form-data，
  標頭 xi-api-key；欄位 model_id（scribe_v2）、file、language_code（ISO-639-3：zho／jpn／eng）、tag_audio_events；回 {text, language_code, audio_duration_secs}。
  跟 OpenRouter 轉錄同一條路：精修逐句送、只換文字、時間沿用草稿句界、逐詞時間交給本機對齊；金鑰只在後端；要同意遠端。
- 一個遠端來源可以有多個轉錄模型：transcription_model（主要，清單鍵＝openrouter／elevenlabs）＋ transcription_models（全部）；
  其他模型的清單鍵是「來源:模型名稱」，例如 openrouter:microsoft/mai-transcribe-2。
- ElevenLabs 只做轉錄：不能拿來做 AI 分析、校字。HTTP 一律是本機替身，不連網。"""
import email.parser
import email.policy
import io
import json
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import contextmanager
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import config as app_config
from app.studio import asr, asr_models, providers
from app.studio.providers import ProviderError
from test_studio_remote_asr import provider as openrouter

MAI = 'microsoft/mai-transcribe-2'
META = 'meta/muse-voice-transcribe-1.0'


def elevenlabs(url='https://api.elevenlabs.io/v1', **extra):
    base = next(dict(p) for p in providers.DEFAULT_PROVIDERS if p['id'] == 'api-elevenlabs')
    return {**base, 'base_url': url, **extra}


@contextmanager
def fake_elevenlabs(routes):
    """ElevenLabs 替身：記錄標頭與 multipart 欄位。routes: {(method, path): (status, payload)}。"""
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _serve(self, method):
            path = self.path.split('?')[0]
            raw = self.rfile.read(int(self.headers.get('Content-Length') or 0))
            fields = {}
            kind = self.headers.get('Content-Type') or ''
            if kind.startswith('multipart/'):
                message = email.parser.BytesParser(policy=email.policy.default).parsebytes(
                    b'Content-Type: ' + kind.encode() + b'\r\n\r\n' + raw)
                for part in message.iter_parts():
                    name = part.get_param('name', header='content-disposition')
                    payload = part.get_payload(decode=True)
                    fields[name] = payload if part.get_filename() else payload.decode('utf-8')
            calls.append(dict(method=method, path=path, headers=dict(self.headers), fields=fields, raw=raw))
            status, payload = routes.get((method, path), (404, {'detail': 'not found'}))
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
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f'http://127.0.0.1:{server.server_address[1]}/v1', calls
    finally:
        server.shutdown()
        server.server_close()


def test_elevenlabs_is_a_default_transcription_only_provider():
    item = elevenlabs()
    assert item['adapter'] == 'elevenlabs' and item['base_url'] == 'https://api.elevenlabs.io/v1'
    assert item['transcription_model'] == 'scribe_v2' and item['api_key_env'] == 'ELEVENLABS_API_KEY' and item['allow_remote'] is True
    # 金鑰走 xi-api-key，不送 Authorization，也不帶 JSON 的 Content-Type（要送 multipart）
    settings = providers._settings(item, 'sk_test')
    assert settings['headers'].get('xi-api-key') == 'sk_test' and 'Authorization' not in settings['headers']
    assert 'Content-Type' not in settings['headers']
    # 只做轉錄：拿來校字直接回穩定錯誤碼，不會送出
    cues = [dict(id='c1', start_us=0, end_us=1000000, raw_text='你好', accepted_text='你好')]
    with pytest.raises(ProviderError) as caught:
        providers.correct(cues, item, secret='sk_test', base_revision='r1')
    assert str(caught.value) == 'PROVIDER_TRANSCRIPTION_ONLY'


def test_elevenlabs_transcribe_sends_multipart_with_the_xi_api_key():
    from app.studio import remote_asr
    audio = (np.sin(np.linspace(0, 200, 24000)) * .2).astype(np.float32)
    answer = {'text': ' 你好 世界 ', 'language_code': 'zho', 'language_probability': .98, 'audio_duration_secs': 1.5, 'words': []}
    with fake_elevenlabs({('POST', '/v1/speech-to-text'): (200, answer)}) as (url, calls):
        result = remote_asr.transcribe_text(audio, provider=elevenlabs(url), secret='sk_test', language='zh')
    assert result['text'] == '你好 世界' and result['usage'] == {'seconds': 1.5}
    [call] = calls
    assert call['headers'].get('xi-api-key') == 'sk_test' and 'Authorization' not in call['headers']
    fields = call['fields']
    assert fields['model_id'] == 'scribe_v2' and fields['language_code'] == 'zho' and fields['tag_audio_events'] == 'false'
    with wave.open(io.BytesIO(fields['file'])) as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getnframes()) == (16000, 1, 2, 24000)
    # 語言沿用草稿判定：日文、英文用 ISO-639-3；不確定就不送，讓服務自己偵測
    for language, code in (('ja', 'jpn'), ('en', 'eng'), (None, None)):
        with fake_elevenlabs({('POST', '/v1/speech-to-text'): (200, answer)}) as (url, calls):
            remote_asr.transcribe_text(audio, provider=elevenlabs(url), secret='sk_test', language=language)
        assert calls[0]['fields'].get('language_code') == code


def test_elevenlabs_errors_are_stable_codes(monkeypatch):
    from app.studio import remote_asr
    monkeypatch.setattr(providers.time, 'sleep', lambda seconds: None)
    audio = np.zeros(16000, dtype=np.float32)
    for status, code in ((401, 'PROVIDER_AUTH_FAILED'), (402, 'PROVIDER_CREDITS_EXHAUSTED'), (422, 'PROVIDER_HTTP_422')):
        with fake_elevenlabs({('POST', '/v1/speech-to-text'): (status, {'detail': {'status': 'x', 'message': 'x'}})}) as (url, calls):
            with pytest.raises(ProviderError) as caught:
                remote_asr.transcribe_text(audio, provider=elevenlabs(url), secret='sk_test', language='zh')
        assert str(caught.value) == code and len(calls) == 1
    with fake_elevenlabs({('POST', '/v1/speech-to-text'): (429, {'detail': {'status': 'too_many_concurrent_requests'}})}) as (url, calls):
        with pytest.raises(ProviderError) as caught:
            remote_asr.transcribe_text(audio, provider=elevenlabs(url), secret='sk_test', language='zh')
    assert str(caught.value) == 'PROVIDER_RATE_LIMITED' and len(calls) == 2  # 限流重試一次
    with pytest.raises(ProviderError) as caught:  # 沒有金鑰不送出
        remote_asr.transcribe_text(audio, provider=elevenlabs(), secret=None, language='zh')
    assert str(caught.value) == 'PROVIDER_SECRET_MISSING'


MISSING_STT = {'detail': {'type': 'authentication_error', 'code': 'unauthorized', 'status': 'missing_permissions',
                          'message': 'The API key you used is missing the permission speech_to_text to execute this operation.'}}
NO_FILE = {'detail': [{'loc': ['body', 'file'], 'msg': 'field required', 'type': 'missing'}]}


def test_elevenlabs_connection_test_does_not_spend_money(monkeypatch):
    """測試連線：GET /models 驗金鑰；再送一個「不附音訊」的轉錄請求驗 Speech to Text 權限（ElevenLabs 先查權限、再查檔案：
    有權限回 422 缺檔案、沒權限回 401 missing_permissions，兩種都不會轉錄、不花錢）。2026-09-21 真跑：只查 /models 會顯示 ready，
    實際轉錄全部 401。限定權限但能轉錄的金鑰（/models 回 missing_permissions）仍算可用。"""
    monkeypatch.delenv('ELEVENLABS_API_KEY', raising=False)
    routes = {('GET', '/v1/models'): (200, [{'model_id': 'eleven_multilingual_v2'}]), ('POST', '/v1/speech-to-text'): (422, NO_FILE)}
    with fake_elevenlabs(routes) as (url, calls):
        result = providers.probe_status(elevenlabs(url), 'sk_test')
    assert result['status'] == 'ready' and result['model'] == 'scribe_v2' and [c['path'] for c in calls] == ['/v1/models', '/v1/speech-to-text']
    assert calls[0]['headers'].get('xi-api-key') == 'sk_test' and 'file' not in calls[1]['fields']  # 不附音訊
    scoped = {'detail': {'status': 'missing_permissions', 'message': 'The API key you used is missing the permission models_read'}}
    with fake_elevenlabs({**routes, ('GET', '/v1/models'): (401, scoped)}) as (url, calls):
        result = providers.probe_status(elevenlabs(url), 'sk_test')
    assert result['status'] == 'ready' and result['detail'] == 'KEY_SCOPED'
    with fake_elevenlabs({**routes, ('POST', '/v1/speech-to-text'): (401, MISSING_STT)}) as (url, calls):
        result = providers.probe_status(elevenlabs(url), 'sk_test')
    assert result['status'] == 'auth_failed' and result['detail'] == 'PERMISSION_MISSING:speech_to_text'
    with fake_elevenlabs({('GET', '/v1/models'): (401, {'detail': {'status': 'invalid_api_key', 'message': 'Invalid API key'}})}) as (url, calls):
        assert providers.probe_status(elevenlabs(url), 'sk_test')['status'] == 'auth_failed'
    assert providers.probe_status(elevenlabs(), None)['status'] == 'secret_missing'


def test_a_key_without_the_speech_to_text_permission_says_so(monkeypatch):
    """缺權限不是金鑰錯：自己的錯誤碼、記下缺哪個權限、不重試；工作訊息講要去哪裡開。"""
    from app.studio import remote_asr
    from app.studio.worker import failure_reason
    monkeypatch.setattr(providers.time, 'sleep', lambda seconds: None)
    with fake_elevenlabs({('POST', '/v1/speech-to-text'): (401, MISSING_STT)}) as (url, calls):
        with pytest.raises(ProviderError) as caught:
            remote_asr.transcribe_text(np.zeros(16000, dtype=np.float32), provider=elevenlabs(url), secret='sk_test', language='zh')
    assert str(caught.value) == 'PROVIDER_PERMISSION_MISSING' and caught.value.permission == 'speech_to_text' and len(calls) == 1
    reason = failure_reason({'code': 'PROVIDER_PERMISSION_MISSING', 'message': MISSING_STT['detail']['message'], 'permission': 'speech_to_text'})
    assert 'Speech to Text' in reason and '權限' in reason and '重新填入金鑰' not in reason
    assert failure_reason({'code': 'PROVIDER_AUTH_FAILED', 'message': 'x'}).startswith('供應者認證失敗')


def config_with_both(**openrouter_extra):
    return {'providers': [{**openrouter('https://openrouter.ai/api/v1', api_key_env='OPENROUTER_API_KEY'), **openrouter_extra},
                          elevenlabs()]}


def test_catalog_lists_every_transcription_model_of_every_remote_source(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'sk-or-env')
    monkeypatch.delenv('ELEVENLABS_API_KEY', raising=False)
    config = config_with_both(transcription_model=META, transcription_models=[META, MAI])
    listed = {m['key']: m for m in asr_models.catalog(tmp_path, config=config, root=tmp_path)}
    assert listed['openrouter']['installed'] is True and META in listed['openrouter']['label']
    mai = listed['openrouter:' + MAI]
    assert mai['installed'] is True and mai['remote'] is True and mai['timestamps'] is False and MAI in mai['label']
    assert mai['provider_label'] == 'OpenRouter' and 'openrouter:' + META not in listed  # 主要模型只列一次
    el = listed['elevenlabs']
    assert el['remote'] is True and el['installed'] is False and el['missing'] == 'secret' and 'scribe_v2' in el['label']
    assert el['provider_label'] == 'ElevenLabs'
    monkeypatch.setenv('ELEVENLABS_API_KEY', 'sk_env')
    assert {m['key']: m for m in asr_models.catalog(tmp_path, config=config, root=tmp_path)}['elevenlabs']['installed'] is True
    # 遠端永遠不當預設
    assert asr_models.default_model({**config, 'default_asr_model': 'elevenlabs'}, tmp_path) != 'elevenlabs'


def test_remote_settings_resolve_the_model_named_in_the_key(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'sk-or-env')
    monkeypatch.setenv('ELEVENLABS_API_KEY', 'sk_env')
    config = config_with_both(transcription_model=META, transcription_models=[META, MAI])
    assert asr_models.remote_settings(config, tmp_path)['model'] == META  # 舊呼叫（不帶鍵）＝OpenRouter 主要模型
    assert asr_models.remote_settings(config, tmp_path, 'openrouter')['model'] == META
    picked = asr_models.remote_settings(config, tmp_path, 'openrouter:' + MAI)
    assert picked['model'] == MAI and picked['secret'] == 'sk-or-env' and picked['provider']['id'] == 'api-openrouter'
    el = asr_models.remote_settings(config, tmp_path, 'elevenlabs')
    assert el['model'] == 'scribe_v2' and el['secret'] == 'sk_env' and el['provider']['id'] == 'api-elevenlabs'
    assert asr_models.remote_settings(config, tmp_path, 'openrouter:unknown/model') is None  # 沒登記的模型名稱不收
    assert asr_models.is_remote('openrouter:' + MAI) and asr_models.is_remote('elevenlabs') and not asr_models.is_remote('large-v3')
    assert asr_models.timestamps('elevenlabs') is False and asr_models.runner('openrouter:' + MAI) == 'remote'


def test_refine_sends_each_sentence_to_the_model_named_in_the_key(monkeypatch):
    from app.studio import remote_asr
    sent = []

    def fake_transcribe(audio, *, provider, secret, language):
        sent.append((provider['id'], provider['transcription_model'], secret, language))
        return dict(text='遠端文字', avg_logprob=None, compression_ratio=None, usage={'seconds': 1.0})
    monkeypatch.setattr(remote_asr, 'transcribe_text', fake_transcribe)
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=lambda *a, **k: pytest.fail('不應載入本機模型')))
    monkeypatch.setattr(asr, '_load_audio', lambda path: np.ones(16000 * 10, dtype=np.float32) * .1)
    cue = dict(id='a', start_us=1000000, end_us=2500000, raw_text='草稿', accepted_text='草稿', lang='zh', alignment_status='segment', words=[])
    remote = dict(provider={**openrouter('https://openrouter.ai/api/v1'), 'transcription_model': META, 'transcription_models': [META, MAI]},
                  secret='sk-or', model=MAI)
    result = asr.refine([cue], 'audio.wav', device='cpu', model='openrouter:' + MAI, remote=remote, cue_ids=['a'],
                        max_refine_audio_ratio=1.0, context_us=0)
    row = next(c for c in result['cues'] if c.get('origin_cue_ids') == ['a'])
    assert row['raw_text'] == '遠端文字' and row['refinement_provenance']['remote_model'] == MAI
    assert sent == [('api-openrouter', MAI, 'sk-or', 'zh')]
    remote = dict(provider=elevenlabs(), secret='sk_el', model='scribe_v2')
    sent.clear()
    result = asr.refine([cue], 'audio.wav', device='cpu', model='elevenlabs', remote=remote, cue_ids=['a'], max_refine_audio_ratio=1.0, context_us=0)
    assert sent == [('api-elevenlabs', 'scribe_v2', 'sk_el', 'zh')] and result['usage']['seconds'] == 1.0


def test_worker_hands_the_chosen_remote_source_to_refine(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from test_studio_refine_wiring import completed, prepare
    from app.studio.worker import Worker
    store, job, cues = prepare(tmp_path, {'model': 'elevenlabs', 'cue_ids': ['cue']})
    (store.root / 'config.json').write_text(json.dumps(config_with_both()), encoding='utf-8')
    monkeypatch.setenv('ELEVENLABS_API_KEY', 'sk_env')
    refine = Mock(return_value=completed(cues))
    monkeypatch.setattr('app.studio.asr.refine', refine)
    Worker(store, job).refine_align()
    remote = refine.call_args.kwargs['remote']
    assert remote['secret'] == 'sk_env' and remote['provider']['id'] == 'api-elevenlabs' and remote['model'] == 'scribe_v2'


def test_api_guards_and_lists_the_new_transcription_sources(tmp_path, monkeypatch):
    from app.studio.api import create_app
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    monkeypatch.delenv('ELEVENLABS_API_KEY', raising=False)
    monkeypatch.setattr(app_config, 'MODELS_DIR', tmp_path / 'models')
    with TestClient(create_app(tmp_path / 'state', start_workers=False)) as c:
        c.get('/v1/session')
        providers_listed = {p['id']: p for p in c.get('/v1/providers').json()['items']}
        assert providers_listed['api-elevenlabs']['adapter'] == 'elevenlabs' and providers_listed['api-elevenlabs']['local'] is False
        project = c.post('/v1/projects', json={'name': '遠端轉錄來源'}).json()['project_id']
        source = c.post(f'/v1/projects/{project}/sources', json={'kind': 'youtube', 'url': 'https://www.youtube.com/watch?v=Yn2mE6_tMC8'}).json()['source_id']
        body = {'kind': 'analyze', 'source_id': source, 'profile': 'quality', 'asr_model': 'elevenlabs'}
        no_consent = c.post(f'/v1/projects/{project}/jobs', json=body)
        assert no_consent.status_code == 422 and no_consent.json()['error']['code'] == 'REMOTE_CONSENT_REQUIRED'
        assert 'ElevenLabs' in no_consent.json()['error']['message']
        no_key = c.post(f'/v1/projects/{project}/jobs', json={**body, 'remote_consent': True})
        assert no_key.status_code == 409 and 'ElevenLabs' in no_key.json()['error']['message'] and '金鑰' in no_key.json()['error']['message']
        # 新增 OpenRouter 轉錄模型：存進 transcription_models，清單就多一個選項
        current = providers_listed['api-openrouter']
        fields = ('id', 'adapter', 'base_url', 'model', 'gpu_ownership', 'timeout_sec', 'max_retries', 'max_context_chars', 'max_output_tokens',
                  'response_format_mode', 'reasoning_effort', 'allow_remote', 'api_key_env', 'transcription_model', 'transcription_models')
        saved = c.put('/v1/providers/api-openrouter', json={k: v for k, v in {**current, 'transcription_models': [META, MAI]}.items() if k in fields})
        assert saved.status_code == 200, saved.text
        assert c.get('/v1/providers').json()['items'][[p['id'] for p in c.get('/v1/providers').json()['items']].index('api-openrouter')]['transcription_models'] == [META, MAI]
        monkeypatch.setenv('OPENROUTER_API_KEY', 'sk-or-env')
        monkeypatch.setenv('ELEVENLABS_API_KEY', 'sk_env')
        keys = [m['key'] for m in c.get('/v1/capabilities').json()['asr_models']]
        assert 'openrouter' in keys and 'openrouter:' + MAI in keys and 'elevenlabs' in keys
        for key in ('elevenlabs', 'openrouter:' + MAI):
            ok = c.post(f'/v1/projects/{project}/jobs', json={**body, 'asr_model': key, 'remote_consent': True})
            assert ok.status_code == 202 and ok.json()['body']['asr_model'] == key
        unknown = c.post(f'/v1/projects/{project}/jobs', json={**body, 'asr_model': 'openrouter:unknown/model', 'remote_consent': True})
        assert unknown.status_code == 409 and unknown.json()['error']['code'] == 'MODEL_NOT_INSTALLED'
        bogus = c.post(f'/v1/projects/{project}/jobs', json={**body, 'asr_model': 'whatever-model'})
        assert bogus.status_code == 422
        # ElevenLabs 只做轉錄：不能拿來校字／AI 分析／拆單詞
        transcript = c.post(f'/v1/projects/{project}/jobs', json={'kind': 'correct', 'transcript_revision': 'x', 'provider_id': 'api-elevenlabs',
                                                                    'remote_consent': True})
        assert transcript.status_code == 422 and transcript.json()['error']['code'] == 'PROVIDER_TRANSCRIPTION_ONLY'
        words = c.post('/v1/text/keywords', json={'text': '彭彭', 'provider_id': 'api-elevenlabs', 'remote_consent': True})
        assert words.status_code == 422 and words.json()['error']['code'] == 'PROVIDER_TRANSCRIPTION_ONLY'
        assert 'sk_env' not in c.get('/v1/providers').text and 'sk_env' not in c.get('/v1/capabilities').text


def test_existing_config_gets_elevenlabs_once(tmp_path):
    from app.studio.api import create_app
    state = tmp_path / 'state'
    state.mkdir()
    old = [dict(p) for p in providers.DEFAULT_PROVIDERS if p['id'] != 'api-elevenlabs']
    (state / 'config.json').write_text(json.dumps({'token': 't', 'roots': {}, 'providers': old}), encoding='utf-8')
    with TestClient(create_app(state, start_workers=False)):
        pass
    saved = json.loads((state / 'config.json').read_text(encoding='utf-8'))
    assert [p['id'] for p in saved['providers']].count('api-elevenlabs') == 1 and 'api-elevenlabs' in saved['provider_defaults_added']
    # 使用者刪掉之後不再自動加回
    saved['providers'] = [p for p in saved['providers'] if p['id'] != 'api-elevenlabs']
    (state / 'config.json').write_text(json.dumps(saved), encoding='utf-8')
    with TestClient(create_app(state, start_workers=False)):
        pass
    assert 'api-elevenlabs' not in [p['id'] for p in json.loads((state / 'config.json').read_text(encoding='utf-8'))['providers']]


def test_cli_accepts_the_new_transcription_keys():
    from app.studio import cli
    parser = cli._parser()
    for key in ('elevenlabs', 'openrouter:' + MAI, 'elevenlabs:scribe_v1'):
        args = parser.parse_args(['analyze', '--project', 'p', '--source', 's', '--profile', 'quality', '--asr-model', key, '--remote-consent'])
        assert args.asr_model == key
    with pytest.raises((SystemExit, cli.CLIError)):
        parser.parse_args(['analyze', '--project', 'p', '--source', 's', '--asr-model', 'whatever-model'])
