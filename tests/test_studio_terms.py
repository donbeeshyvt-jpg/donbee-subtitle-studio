"""2026-09-20 使用者第 4、5 點：
4. 校字詞彙：說明怎麼送進模型；可匯入文字檔；逗號或空格分隔都可以；也可以輸入故事大綱讓模型參考裡面的用詞（網頁與 API 流程都要）。
5. 轉錄術語提示：與校字詞彙分開，逗號分隔、可匯入文字檔；「內容拆解單詞」依當下模型把內容整理成逗號分隔的關鍵詞；送進轉錄模型。
HTTP 供應者一律是本機替身。"""
import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.studio import asr, providers, terms
from test_studio_provider_setup import fake_provider


def test_split_terms_accepts_commas_newlines_or_spaces_without_breaking_multiword_names():
    assert terms.split_terms('彭彭, 斯斯，海海、PICO PARK;天照堂\n彭彭') == ['彭彭', '斯斯', '海海', 'PICO PARK', '天照堂']
    assert terms.split_terms('彭彭 斯斯  海海') == ['彭彭', '斯斯', '海海']  # 只有空格：照空格分
    assert terms.split_terms('') == [] and len(terms.split_terms(','.join(f'詞{i}' for i in range(300)))) == 100
    assert terms.join_terms(['彭彭', '斯斯']) == '彭彭, 斯斯'


def test_rule_keywords_pick_names_and_repeated_terms_from_free_text():
    story = ('本集《PICO PARK》由彭彭、斯斯、海海三人合作闖關。彭彭負責開門，斯斯常常把海海推下去，'
             '最後三人在「天照堂」頻道的直播中一起破關。PICO PARK 是一款合作遊戲。')
    keywords = terms.rule_keywords(story)
    for expected in ('PICO PARK', '彭彭', '斯斯', '海海', '天照堂'):
        assert expected in keywords, keywords
    assert len(keywords) == len(set(keywords)) and all(len(k) <= 20 for k in keywords)


def test_llm_keywords_use_the_current_text_model_and_fall_back_to_rules():
    body = {'choices': [{'message': {'content': json.dumps({'keywords': ['彭彭', '斯斯', 'PICO PARK', '彭彭']}, ensure_ascii=False)}}]}
    config = {'id': 'local', 'base_url': None, 'model': 'gemma', 'response_format_mode': 'json_schema'}
    with fake_provider({('POST', '/v1/chat/completions'): (200, body)}) as (url, calls):
        result = terms.extract_keywords('彭彭和斯斯玩 PICO PARK', provider={**config, 'base_url': url}, secret=None)
    assert result == {'keywords': ['彭彭', '斯斯', 'PICO PARK'], 'method': 'llm', 'joined': '彭彭, 斯斯, PICO PARK', 'model': 'gemma'}
    sent = calls[0][2]
    assert sent['response_format']['type'] == 'json_schema' and '彭彭和斯斯玩 PICO PARK' in json.dumps(sent, ensure_ascii=False)
    with fake_provider({('POST', '/v1/chat/completions'): (500, {'error': 'down'})}) as (url, _):
        fallback = terms.extract_keywords('本集由彭彭與斯斯主持。彭彭很強。', provider={**config, 'base_url': url, 'max_retries': 0}, secret=None)
    assert fallback['method'] == 'rules' and '彭彭' in fallback['keywords'] and fallback['warning'] == 'PROVIDER_SERVER_ERROR'
    assert terms.extract_keywords('彭彭與斯斯。彭彭。', provider=None, secret=None)['method'] == 'rules'


def test_correction_sends_the_reference_text_and_trusts_terms_found_in_it():
    cues = [dict(id='c1', start_us=0, end_us=1000000, text='今天來玩屁股趴客', raw_text='今天來玩屁股趴客', accepted_text='今天來玩屁股趴客')]
    patch = {'patches': [{'cue_id': 'c1', 'original_text': '今天來玩屁股趴客', 'replacement_text': '今天來玩皮可帕克', 'reason': '參考資料中的遊戲名稱'}]}
    body = {'choices': [{'message': {'content': json.dumps(patch, ensure_ascii=False)}}]}
    with fake_provider({('POST', '/v1/chat/completions'): (200, body)}) as (url, calls):
        result = providers.correct(cues, {'base_url': url, 'model': 'm', 'response_format_mode': 'json_schema', 'max_retries': 0},
                                   glossary=None, base_revision='r1', title='EP7', reference='本集玩的遊戲是皮可帕克（PICO PARK），由彭彭主持。')
    sent = json.loads(calls[0][2]['messages'][1]['content'])
    assert sent['context']['reference'].startswith('本集玩的遊戲是皮可帕克')
    assert 'context.reference' in calls[0][2]['messages'][0]['content']  # 系統提示說明參考資料怎麼用
    [accepted] = result['patches']
    assert accepted['confidence'] == 'high' and accepted.get('reference_backed') is True  # 用詞出現在參考資料裡：高信心


def test_contracts_and_worker_carry_reference_text_and_asr_hints(tmp_path, monkeypatch):
    from app.studio.contracts import JobRequest
    request = JobRequest(kind='correct', transcript_revision='t', provider_id='p', glossary=['彭彭'], reference_text='大綱')
    assert request.reference_text == '大綱'
    assert JobRequest(kind='analyze', source_id='s', asr_hints='彭彭, 斯斯').asr_hints == '彭彭, 斯斯'
    with pytest.raises(Exception):
        JobRequest(kind='correct', transcript_revision='t', reference_text='長' * 6001)


def test_asr_hints_become_faster_whisper_hotwords_in_draft_and_refine(monkeypatch):
    seen = []

    def make_model(model, **kwargs):
        def transcribe(audio, **options):
            seen.append((model, options.get('hotwords')))
            row = SimpleNamespace(text='彭彭好', start=.1, end=.9, words=[], avg_logprob=-.3, compression_ratio=1)
            return iter([row]), SimpleNamespace(language='zh', duration=1.0, duration_after_vad=1.0)
        return SimpleNamespace(transcribe=transcribe)
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=make_model))
    monkeypatch.setattr(asr, '_load_audio', lambda path: np.ones(16000 * 2, dtype=np.float32) * .1)
    asr.draft(np.ones(16000, dtype=np.float32) * .1, model='turbo', device='cpu', language_policy='zh', hints='彭彭, 斯斯')
    cue = dict(id='a', start_us=0, end_us=1000000, raw_text='碰碰好', accepted_text='碰碰好', lang='zh', alignment_status='segment', words=[])
    asr.refine([cue], 'audio.wav', device='cpu', cue_ids=['a'], max_refine_audio_ratio=1.0, context_us=0, hints='彭彭, 斯斯')
    assert seen == [('turbo', '彭彭, 斯斯'), ('large-v3', '彭彭, 斯斯')]
    seen.clear()
    asr.draft(np.ones(16000, dtype=np.float32) * .1, model='turbo', device='cpu', language_policy='zh')
    assert seen == [('turbo', None)]  # 沒有提示就不送


def test_keywords_endpoint_uses_the_selected_provider_and_asks_consent_for_remote_ones(tmp_path, monkeypatch):
    from app.studio.api import create_app
    body = {'choices': [{'message': {'content': json.dumps({'keywords': ['彭彭', '海海']}, ensure_ascii=False)}}]}
    with fake_provider({('POST', '/v1/chat/completions'): (200, body)}) as (url, _):
        app = create_app(tmp_path / 'state', start_workers=False)
        app.state.config['providers'].append({'id': 'fake-local', 'adapter': 'openai_compatible', 'base_url': url, 'model': 'm',
                                              'response_format_mode': 'json_schema', 'max_retries': 0})
        with TestClient(app) as c:
            c.get('/v1/session')
            llm = c.post('/v1/text/keywords', json={'text': '彭彭與海海', 'provider_id': 'fake-local'})
            assert llm.status_code == 200 and llm.json()['joined'] == '彭彭, 海海' and llm.json()['method'] == 'llm'
            rules = c.post('/v1/text/keywords', json={'text': '彭彭與海海。彭彭。'})
            assert rules.status_code == 200 and rules.json()['method'] == 'rules'
            remote = c.post('/v1/text/keywords', json={'text': '彭彭', 'provider_id': 'api-openrouter'})
            assert remote.status_code == 422 and remote.json()['error']['code'] == 'REMOTE_CONSENT_REQUIRED'
            assert c.post('/v1/text/keywords', json={'text': ''}).status_code == 422


def test_openrouter_carries_keyword_split_and_reference_correction_through_the_chat_port(monkeypatch):
    # 第 8 點：OpenRouter 只要填金鑰就能用這些新功能（語言模型端口）；轉錄端口沒有 prompt 欄位，術語提示不送（官網 create-transcription）
    captured = []

    def fake_request(settings, method, path, body=None):
        captured.append((settings['url'], settings['headers'].get('Authorization'), path, body))
        if 'keywords' in json.dumps(body.get('response_format', {})):
            return {'choices': [{'message': {'content': json.dumps({'keywords': ['彭彭', 'PICO PARK']}, ensure_ascii=False)}}]}
        patch = {'patches': [{'cue_id': 'c1', 'original_text': '今天玩屁股趴客', 'replacement_text': '今天玩皮可帕克', 'reason': '參考資料的遊戲名'}]}
        return {'choices': [{'message': {'content': json.dumps(patch, ensure_ascii=False)}}], 'usage': {'prompt_tokens': 10}}
    monkeypatch.setattr(providers, '_request', fake_request)
    monkeypatch.setenv('OPENROUTER_API_KEY', 'sk-or-test')
    openrouter = next(dict(p) for p in providers.DEFAULT_PROVIDERS if p['id'] == 'api-openrouter')
    result = terms.extract_keywords('彭彭玩 PICO PARK', provider=openrouter, secret=None)
    assert result['method'] == 'llm' and result['joined'] == '彭彭, PICO PARK' and result['model'] == 'deepseek/deepseek-v4.1-flash'
    url, auth, path, body = captured[0]
    assert url == 'https://openrouter.ai/api/v1' and auth == 'Bearer sk-or-test' and path == '/chat/completions'
    assert body['provider'] == {'require_parameters': True} and body['response_format']['type'] == 'json_schema'
    cues = [dict(id='c1', start_us=0, end_us=1000000, raw_text='今天玩屁股趴客', accepted_text='今天玩屁股趴客')]
    corrected = providers.correct(cues, openrouter, ['彭彭'], base_revision='r1', title='EP7', reference='遊戲是皮可帕克')
    sent = json.loads(captured[1][3]['messages'][1]['content'])
    # 2026-09-21 提示詞根本修正：使用者的字幕格式（台灣繁體、不保留標點）一起放進 context.output
    assert sent['context'] == {'glossary': ['彭彭'], 'title': 'EP7', 'reference': '遊戲是皮可帕克',
                               'output': {'script': 'zh-Hant-TW', 'punctuation': 'none'}}
    assert captured[1][3]['provider'] == {'require_parameters': True}
    assert corrected['patches'][0]['reference_backed'] is True and corrected['reference'] == {'chars_total': 7, 'chars_sent': 7, 'truncated': False}


def test_long_reference_is_cut_to_half_the_context_budget_and_reported():
    from app.studio.providers import _fit_reference
    text, info = _fit_reference('劇' * 3000, {'max_context_chars': 6000}, {'glossary': [], 'title': ''})
    assert len(json.dumps(text, ensure_ascii=False).encode('utf-8')) <= 3000 and info['truncated'] is True
    assert info['chars_total'] == 3000 and info['chars_sent'] == len(text) > 900


def test_correction_prompt_tells_the_model_to_apply_glossary_spellings_to_near_homophones():
    """使用者 2026-09-20：按了校字逐字稿卻沒變化。真跑（gemma-4-e4b、51 句）只回 3 處標點差異 → 提示詞要明講：
    句子裡出現詞彙表或參考資料的近音／近形寫法時一定要改成正確寫法。"""
    captured = []

    def fake_request(settings, method, path, body=None):
        captured.append(body)
        return {'choices': [{'message': {'content': json.dumps({'patches': []})}}]}
    from unittest.mock import patch as mock_patch
    cues = [dict(id='c1', start_us=0, end_us=1000000, raw_text='碰碰你好', accepted_text='碰碰你好')]
    with mock_patch.object(providers, '_request', fake_request):
        providers.correct(cues, {'base_url': 'http://127.0.0.1:1234/v1', 'model': 'm', 'response_format_mode': 'json_schema', 'max_retries': 0},
                          ['彭彭'], base_revision='r1', title='EP7', reference='彭彭主持')
    system = captured[0]['messages'][0]['content']
    assert '近音' in system and '一定要改' in system and '彭彭' in system  # 有規則也有例子


def test_rewrite_mode_lets_context_fixes_through_while_conservative_keeps_them_out():
    """使用者 2026-09-20：「我要的是一上下文處理逐字稿就會每一句套上」→ 新增校字強度：
    保守＝只改明顯錯字（現行守門）；積極＝依上下文逐句改寫，語意類修改照樣套用，只靠新舊比對與整批還原把關。"""
    patch = {'patches': [{'cue_id': 'c1', 'original_text': '原來會打死都還沒', 'replacement_text': '原來會打死隊友',
                          'reason': '依上下文語意修正'}]}
    body = {'choices': [{'message': {'content': json.dumps(patch, ensure_ascii=False)}}]}
    cues = [dict(id='c1', start_us=0, end_us=2000000, raw_text='原來會打死都還沒', accepted_text='原來會打死都還沒')]
    config = {'model': 'm', 'response_format_mode': 'json_schema', 'max_retries': 0}
    with fake_provider({('POST', '/v1/chat/completions'): (200, body)}) as (url, calls):
        conservative = providers.correct(cues, {**config, 'base_url': url}, base_revision='r1')
    assert conservative['patches'] == [] and conservative['rejected_patches'][0]['rejection_reasons']  # 保守：擋下語意改寫
    with fake_provider({('POST', '/v1/chat/completions'): (200, body)}) as (url, calls):
        rewrite = providers.correct(cues, {**config, 'base_url': url}, base_revision='r1', mode='rewrite')
    assert [p['replacement_text'] for p in rewrite['patches']] == ['原來會打死隊友'] and rewrite['rejected_patches'] == []
    assert rewrite['mode'] == 'rewrite' and conservative['mode'] == 'conservative'
    system = calls[0][2]['messages'][0]['content']
    assert '逐句檢查' in system and '從前後文看得出原話' in system  # 積極模式的提示詞要求逐句處理（2026-09-21 改寫後的說法）
    from app.studio.contracts import JobRequest
    assert JobRequest(kind='correct', transcript_revision='t', correction_mode='rewrite').correction_mode == 'rewrite'
    assert JobRequest(kind='correct', transcript_revision='t').correction_mode == 'conservative'


def test_local_provider_can_follow_whatever_model_is_loaded():
    """使用者 2026-09-20：「本地模型就依照當前載入的即可」→ provider 的 model 設成 auto 時，
    送出前先問服務目前載入哪一個模型（略過向量模型），探測也回報實際用的那一個。"""
    listing = {'data': [{'id': 'text-embedding-nomic'}, {'id': 'google/gemma-4-e4b'}, {'id': 'qwen/qwen3.8-27b'}]}
    answer = {'choices': [{'message': {'content': json.dumps({'keywords': ['彭彭']}, ensure_ascii=False)}}]}
    routes = {('GET', '/v1/models'): (200, listing), ('POST', '/v1/chat/completions'): (200, answer)}
    with fake_provider(routes) as (url, calls):
        config = {'id': 'local-lmstudio', 'base_url': url, 'model': 'auto', 'response_format_mode': 'json_schema', 'max_retries': 0}
        result = terms.extract_keywords('彭彭', provider=config, secret=None)
        assert result['model'] == 'google/gemma-4-e4b'  # 跳過向量模型，用第一個對話模型
        assert [c[2]['model'] for c in calls if c[1].endswith('chat/completions')] == ['google/gemma-4-e4b']
        probe = providers.probe_status(config)
    assert probe['model'] == 'google/gemma-4-e4b' and probe['requested_model'] == 'auto'
    assert probe['status'] in ('ready', 'structured_output_unsupported'), probe
    # 一個都沒載入時講清楚
    with fake_provider({('GET', '/v1/models'): (200, {'data': []})}) as (url, _):
        empty = providers.probe_status({'id': 'local-lmstudio', 'base_url': url, 'model': 'auto', 'max_retries': 0})
    assert empty['status'] == 'model_not_loaded' and empty['detail'] == 'no_model_loaded'


def test_the_three_default_ports_follow_the_loaded_model_and_openrouter_keeps_its_configured_one():
    ids = {p['id']: p for p in providers.DEFAULT_PROVIDERS}
    assert ids['local-lmstudio']['model'] == 'auto' and ids['local-llamacpp']['model'] == 'auto'
    assert ids['api-openrouter']['model'] == 'deepseek/deepseek-v4.1-flash'  # 遠端照後台設定（2026-09-21 使用者指定）
    # 舊設定檔自動升級
    old = [{'id': 'local-lmstudio', 'model': 'google/gemma-4-e4b'}, {'id': 'local-llamacpp', 'model': 'loaded-gguf'},
           {'id': 'local-lmstudio-3b', 'model': 'dongbi-qwen3b-cpu'}]
    assert providers.upgrade_default_providers(old) is True
    assert [p['model'] for p in old] == ['auto', 'auto', 'dongbi-qwen3b-cpu']  # 使用者自建的不動
