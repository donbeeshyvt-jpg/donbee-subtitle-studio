"""2026-09-20 使用者：加入 OpenRouter API，可以替換，有轉錄語音端口也有語言模型端口。
轉錄端口：POST {base}/audio/transcriptions，JSON {model, input_audio:{data: base64, format}, language}（OpenRouter 官網 STT 文件）；
單次 60 秒處理上限 → 精修逐句送、只換文字（時間沿用草稿句界＋本機逐詞對齊），與 Breeze-ASR-26 同一條路。
聲音送到遠端要明確同意（remote_consent），且需要金鑰（只在後端環境變數或 secrets 檔）。HTTP 一律是本機替身，不連網。"""
import base64
import io
import json
import sys
import wave
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import config as app_config
from app.studio import asr, asr_models, providers
from app.studio.providers import ProviderError
from app.studio.store import StudioError
from test_studio_provider_setup import fake_provider

ROUTE = ('POST', '/v1/audio/transcriptions')


def provider(url, **extra):
    return dict(id='api-openrouter', adapter='openai_compatible', base_url=url, model='openai/gpt-4o-mini',
                transcription_model='openai/whisper-1', allow_remote=True, gpu_ownership='cpu', timeout_sec=60, max_retries=1, **extra)


def test_transcribe_sends_base64_wav_to_the_transcriptions_endpoint():
    from app.studio import remote_asr
    assert remote_asr.DEFAULT_TRANSCRIPTION_MODEL == 'openai/whisper-1'
    audio = (np.sin(np.linspace(0, 200, 24000)) * .2).astype(np.float32)
    with fake_provider({ROUTE: (200, {'text': ' 你好 世界 ', 'usage': {'seconds': 1.5, 'cost': 0.0001}})}) as (url, calls):
        result = remote_asr.transcribe_text(audio, provider=provider(url), secret='sk-test', language='zh')
    assert result['text'] == '你好 世界' and result['usage']['seconds'] == 1.5
    [(method, path, body, auth)] = calls
    assert auth == 'Bearer sk-test' and body['model'] == 'openai/whisper-1' and body['language'] == 'zh'
    assert body['input_audio']['format'] == 'wav' and not body['input_audio']['data'].startswith('data:')  # 官網：純 base64，不加 data: 前綴
    with wave.open(io.BytesIO(base64.b64decode(body['input_audio']['data']))) as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getnframes()) == (16000, 1, 2, 24000)


def test_transcribe_errors_are_stable_codes_and_rate_limits_retry_once(monkeypatch):
    from app.studio import remote_asr
    monkeypatch.setattr(providers.time, 'sleep', lambda seconds: None)
    audio = np.zeros(16000, dtype=np.float32)
    for status, code in ((401, 'PROVIDER_AUTH_FAILED'), (402, 'PROVIDER_CREDITS_EXHAUSTED'), (400, 'PROVIDER_HTTP_400')):
        with fake_provider({ROUTE: (status, {'error': {'message': 'x'}})}) as (url, calls):
            with pytest.raises(ProviderError) as caught:
                remote_asr.transcribe_text(audio, provider=provider(url), secret='sk-test', language='zh')
        assert str(caught.value) == code and len(calls) == 1
    with fake_provider({ROUTE: (429, {'error': {'message': 'slow down'}})}) as (url, calls):
        with pytest.raises(ProviderError) as caught:
            remote_asr.transcribe_text(audio, provider=provider(url), secret='sk-test', language='zh')
    assert str(caught.value) == 'PROVIDER_RATE_LIMITED' and len(calls) == 2  # 限流重試一次
    # 遠端服務一定要設定 allow_remote；缺金鑰不送出
    with pytest.raises(ProviderError) as caught:
        remote_asr.transcribe_text(audio, provider={**provider('https://openrouter.ai/api/v1'), 'allow_remote': False}, secret='sk', language='zh')
    assert str(caught.value) == 'REMOTE_PROVIDER_NOT_SELECTED'
    with pytest.raises(ProviderError) as caught:
        remote_asr.transcribe_text(audio, provider={**provider('https://openrouter.ai/api/v1'), 'api_key_env': 'NO_SUCH_KEY_ENV'}, secret=None, language='zh')
    assert str(caught.value) == 'PROVIDER_SECRET_MISSING'


def test_runaway_remote_output_is_rejected():
    from app.studio import remote_asr
    with fake_provider({ROUTE: (200, {'text': '二二三開曖 ' * 40})}) as (url, _):
        with pytest.raises(asr.RefinementRejected):
            remote_asr.transcribe_text(np.zeros(8000, dtype=np.float32), provider=provider(url), secret='sk', language='zh')


def test_catalog_offers_openrouter_only_when_configured_and_it_is_never_the_default(tmp_path, monkeypatch):
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    config = {'providers': [provider('https://openrouter.ai/api/v1', api_key_env='OPENROUTER_API_KEY')]}
    remote = next(m for m in asr_models.catalog(tmp_path, config=config, root=tmp_path) if m['key'] == 'openrouter')
    assert remote['remote'] is True and remote['installed'] is False and remote['timestamps'] is False and remote['languages'] is None
    assert 'OpenRouter' in remote['label'] and 'openai/whisper-1' in remote['label'] and remote['missing'] == 'secret'
    monkeypatch.setenv('OPENROUTER_API_KEY', 'sk-env')
    assert next(m for m in asr_models.catalog(tmp_path, config=config, root=tmp_path) if m['key'] == 'openrouter')['installed'] is True
    assert asr_models.default_model({**config, 'default_asr_model': 'openrouter'}, tmp_path) != 'openrouter'  # 遠端永遠不當預設
    assert not [m for m in asr_models.catalog(tmp_path, config={'providers': []}, root=tmp_path) if m['key'] == 'openrouter']


def test_refine_with_openrouter_only_replaces_text_and_keeps_draft_timing(monkeypatch, tmp_path):
    from app.studio import remote_asr
    sent = []

    def fake_transcribe(audio, *, provider, secret, language):
        sent.append((len(audio), language, provider['transcription_model'], secret))
        return dict(text='遠端文字' if language == 'zh' else 'remote text', avg_logprob=None, compression_ratio=None, usage={})
    monkeypatch.setattr(remote_asr, 'transcribe_text', fake_transcribe)
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=lambda *a, **k: pytest.fail('不應載入本機模型')))
    monkeypatch.setattr(asr, '_load_audio', lambda path: np.ones(16000 * 10, dtype=np.float32) * .1)
    cue = lambda i, s, e, t, lang='zh': dict(id=i, start_us=s, end_us=e, raw_text=t, accepted_text=t, lang=lang, alignment_status='segment', words=[])
    source = [cue('a', 1000000, 2500000, '草稿'), cue('b', 3000000, 4000000, 'draft', 'en')]
    remote = dict(provider=provider('https://openrouter.ai/api/v1'), secret='sk-test')
    result = asr.refine(source, 'audio.wav', device='cpu', model='openrouter', remote=remote, cue_ids=['a', 'b'], max_refine_audio_ratio=1.0, context_us=300000)
    a = next(c for c in result['cues'] if c.get('origin_cue_ids') == ['a'])
    b = next(c for c in result['cues'] if c.get('origin_cue_ids') == ['b'])
    assert (a['start_us'], a['end_us'], a['raw_text']) == (1000000, 2500000, '遠端文字')
    assert (b['start_us'], b['end_us'], b['raw_text']) == (3000000, 4000000, 'remote text')  # 遠端各語言都處理，不退回 large-v3
    assert a['refinement_provenance']['model'] == 'openrouter' and a['refinement_provenance']['timing'] == 'draft_cue'
    assert a['refinement_provenance']['remote_model'] == 'openai/whisper-1'
    assert sent == [(24000, 'zh', 'openai/whisper-1', 'sk-test'), (16000, 'en', 'openai/whisper-1', 'sk-test')]
    with pytest.raises(StudioError) as caught:  # 沒有遠端設定就不能用
        asr.refine(source, 'audio.wav', device='cpu', model='openrouter', cue_ids=['a'], max_refine_audio_ratio=1.0, context_us=0)
    assert caught.value.code == 'MODEL_NOT_INSTALLED'


def test_worker_hands_the_openrouter_settings_and_secret_to_refine(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from test_studio_refine_wiring import completed, prepare
    from app.studio.worker import Worker
    store, job, cues = prepare(tmp_path, {'model': 'openrouter', 'cue_ids': ['cue']})
    (store.root / 'config.json').write_text(json.dumps({'providers': [provider('https://openrouter.ai/api/v1', api_key_env='OPENROUTER_API_KEY')]}), encoding='utf-8')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'sk-env')
    refine = Mock(return_value=completed(cues))
    monkeypatch.setattr('app.studio.asr.refine', refine)
    Worker(store, job).refine_align()
    remote = refine.call_args.kwargs['remote']
    assert remote['secret'] == 'sk-env' and remote['provider']['transcription_model'] == 'openai/whisper-1'


def test_api_requires_consent_and_a_key_before_sending_audio_to_openrouter(tmp_path, monkeypatch):
    from app.studio.api import create_app
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    monkeypatch.setattr(app_config, 'MODELS_DIR', tmp_path / 'models')
    with TestClient(create_app(tmp_path / 'state', start_workers=False)) as c:
        c.get('/v1/session')
        project = c.post('/v1/projects', json={'name': '遠端轉錄'}).json()['project_id']
        source = c.post(f'/v1/projects/{project}/sources', json={'kind': 'youtube', 'url': 'https://www.youtube.com/watch?v=Yn2mE6_tMC8'}).json()['source_id']
        body = {'kind': 'analyze', 'source_id': source, 'profile': 'quality', 'asr_model': 'openrouter'}
        no_consent = c.post(f'/v1/projects/{project}/jobs', json=body)
        assert no_consent.status_code == 422 and no_consent.json()['error']['code'] == 'REMOTE_CONSENT_REQUIRED'
        no_key = c.post(f'/v1/projects/{project}/jobs', json={**body, 'remote_consent': True})
        assert no_key.status_code == 409 and no_key.json()['error']['code'] == 'MODEL_NOT_INSTALLED' and '金鑰' in no_key.json()['error']['message']
        listed = {m['key']: m for m in c.get('/v1/capabilities').json()['asr_models']}
        assert listed['openrouter']['installed'] is False and listed['openrouter']['remote'] is True
        monkeypatch.setenv('OPENROUTER_API_KEY', 'sk-env')
        ok = c.post(f'/v1/projects/{project}/jobs', json={**body, 'remote_consent': True})
        assert ok.status_code == 202 and ok.json()['body']['asr_model'] == 'openrouter'
        assert 'sk-env' not in c.get('/v1/capabilities').text and 'sk-env' not in c.get('/v1/providers').text
        # 轉錄模型可以替換（與語言模型分開）
        saved = c.get('/v1/providers').json()['items']
        current = next(p for p in saved if p['id'] == 'api-openrouter')
        changed = c.put('/v1/providers/api-openrouter', json={k: v for k, v in {**current, 'transcription_model': 'groq/whisper-large-v3'}.items()
                                                             if k in ('id', 'adapter', 'base_url', 'model', 'gpu_ownership', 'timeout_sec', 'max_retries', 'max_context_chars',
                                                                      'max_output_tokens', 'response_format_mode', 'reasoning_effort', 'allow_remote', 'api_key_env', 'transcription_model')})
        assert changed.status_code == 200, changed.text
        assert 'groq/whisper-large-v3' in next(m['label'] for m in c.get('/v1/capabilities').json()['asr_models'] if m['key'] == 'openrouter')


def test_openrouter_structured_requests_only_route_to_providers_that_support_them():
    # 語言模型端口：OpenRouter 會把請求轉給不同供應商；要求結構化輸出時只轉給支援該參數的（官網 provider routing：require_parameters）
    settings = providers._settings({'base_url': 'https://openrouter.ai/api/v1', 'model': 'openai/gpt-4o-mini', 'allow_remote': True,
                                    'response_format_mode': 'json_schema', 'api_key_env': 'X_KEY'}, secret='sk')
    assert providers._chat_body(settings, messages=[])['provider'] == {'require_parameters': True}
    local = providers._settings({'base_url': 'http://127.0.0.1:1234/v1', 'model': 'm', 'response_format_mode': 'json_schema'})
    assert 'provider' not in providers._chat_body(local, messages=[])


def test_openrouter_age_confirmation_403_is_explained_not_called_a_bad_key():
    """2026-09-21 真呼叫：meta/muse-voice-transcribe-1.0 回 403「需要先在 OpenRouter 設定完成 18 歲以上確認」。
    以前被當成「金鑰無效或權限不足」，而且原因被丟掉 → 使用者只看到「選段精修未成功」。"""
    import httpx
    from app.studio import providers
    body = {'error': {'message': 'This model requires you to complete the following before use: 18+ age confirmation. '
                                 'Confirm at https://openrouter.ai/settings/preferences.', 'code': 403,
                      'metadata': {'missing_attestation_types': ['age_18plus']}}}
    response = httpx.Response(403, json=body, request=httpx.Request('POST', 'https://openrouter.ai/api/v1/audio/transcriptions'))
    error = providers._http_error(response)
    assert str(error) == 'PROVIDER_ATTESTATION_REQUIRED'
    assert error.settings_url == 'https://openrouter.ai/settings/preferences'
    assert '18+' in error.detail and len(error.detail) <= 300
    plain = providers._http_error(httpx.Response(403, json={'error': {'message': 'Forbidden'}},
                                                 request=httpx.Request('POST', 'https://openrouter.ai/api/v1/chat/completions')))
    assert str(plain) == 'PROVIDER_AUTH_FAILED'  # 其他 403 照舊


def test_refine_keeps_the_provider_error_code_for_each_failed_sentence(monkeypatch):
    """每一句失敗都要記下是哪一種錯（以前 ProviderError 沒有 .code，診斷只剩 error_type=ProviderError）。"""
    from app.studio import remote_asr
    from app.studio.providers import ProviderError

    def refuse(audio, *, provider, secret, language):
        raise ProviderError('PROVIDER_ATTESTATION_REQUIRED')
    monkeypatch.setattr(remote_asr, 'transcribe_text', refuse)
    monkeypatch.setattr(asr, '_load_audio', lambda path: np.ones(16000 * 10, dtype=np.float32) * .1)
    cue = dict(id='a', start_us=1000000, end_us=2500000, raw_text='草稿', accepted_text='草稿', lang='zh', alignment_status='segment', words=[])
    remote = dict(provider=provider('https://openrouter.ai/api/v1'), secret='sk-test')
    result = asr.refine([cue], 'audio.wav', device='cpu', model='openrouter', remote=remote, cue_ids=['a'], max_refine_audio_ratio=1.0, context_us=0)
    failed = [item for item in result['routing']['selected'] if item['status'] == 'failed']
    assert failed and failed[0]['error']['code'] == 'PROVIDER_ATTESTATION_REQUIRED'


def test_remote_transcription_cost_is_added_up_in_the_refine_result(monkeypatch):
    """2026-09-21：遠端轉錄每一句都會回 usage.cost，以前沒加總 → 轉錄結果看不出花了多少（只能看帳戶，帳戶計數還會延遲）。"""
    from app.studio import remote_asr
    calls = iter([0.00005, 0.00007])

    def fake(audio, *, provider, secret, language):
        return dict(text='遠端文字', avg_logprob=None, compression_ratio=None, usage={'seconds': 1.2, 'cost': next(calls)})
    monkeypatch.setattr(remote_asr, 'transcribe_text', fake)
    monkeypatch.setattr(asr, '_load_audio', lambda path: np.ones(16000 * 10, dtype=np.float32) * .1)
    cue = lambda i, s, e: dict(id=i, start_us=s, end_us=e, raw_text='草稿', accepted_text='草稿', lang='zh', alignment_status='segment', words=[])
    remote = dict(provider=provider('https://openrouter.ai/api/v1'), secret='sk-test')
    result = asr.refine([cue('a', 1000000, 2500000), cue('b', 3000000, 4000000)], 'audio.wav', device='cpu', model='openrouter',
                        remote=remote, cue_ids=['a', 'b'], max_refine_audio_ratio=1.0, context_us=0)
    assert abs(result['usage']['cost'] - 0.00012) < 1e-12 and abs(result['usage']['seconds'] - 2.4) < 1e-9
    assert result['usage']['requests'] == 2
