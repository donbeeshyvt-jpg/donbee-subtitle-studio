"""2026-09-19 使用者：補上 Breeze-ASR（https://github.com/thc1006/breeze-asr-taigi）轉錄中文，能否銜接？

Breeze-ASR-26 是聯發創新基地從 Whisper large-v2 微調的模型（Apache-2.0）：台語語音→中文字，也訓練過台語／華語夾雜。
（2026-09-19 先只用 Breeze-ASR-26；2026-09-20 使用者要求 26 移除、改用 25 為預設候選，並加入 Qwen3-ASR 比較；
 清單相關測試用 25／Qwen，「沒有時間戳的本機模型」行為改用測試專用清單。）
它與目前的 large-v3 同屬 Whisper 家族，faster-whisper 轉成 CTranslate2 格式後就能直接載入，不需要新引擎。
接法：
- 模型清單新增 `ct2` 類：從官方 repo 只下載 safetensors 與設定／詞彙檔（不下載 .bin／.pkl／.pt 訓練檔），在本機轉成 CTranslate2 float16，
  放在 models/ct2/<key>/，另存來源紀錄；環境頁、API、CLI 的「複製或下載」都走同一個需確認的入口。
- 精修那一趟可以選模型：Breeze 只處理中文段落並固定中文；日文／英文段落照舊用 large-v3（同一種模型的段落排在一起，只載入一次）。
- 沒安裝就選 Breeze：送出前就回清楚的錯誤；Breeze 只用在精修，所以要搭配「平衡」或「精修」。
模型一律用替身，不連網、不載入真模型。"""
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import config as app_config
from app.studio import asr, asr_models, model_store
from app.studio.store import StudioError


def make_ct2(folder, with_tokenizer=True):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'model.bin').write_bytes(b'\0' * 64)
    (folder / 'config.json').write_text('{"alignment_heads": [[10, 12]]}', encoding='utf-8')
    (folder / 'preprocessor_config.json').write_text('{"feature_size": 80}', encoding='utf-8')
    if with_tokenizer:
        (folder / 'tokenizer.json').write_text('{}', encoding='utf-8')
    (folder / 'STUDIO_CT2.json').write_text('{"repo": "MediaTek-Research/Breeze-ASR-26"}', encoding='utf-8')
    return folder


# 測試專用的「沒有時間戳」本機模型（26 移除後，這類行為仍要測）
NOTIME = {'key': 'notime-test', 'repo': 'Example/NoTimestamps', 'revision': 'd' * 40, 'required': False, 'download_bytes': 100,
          'label': 'NoTime（測試）', 'languages': ['zh'], 'license': 'Apache-2.0', 'timestamps': False}


def use_manifest(monkeypatch, *ct2):
    monkeypatch.setattr(asr_models, '_manifest', lambda: {'version': 1, 'ct2': list(ct2), 'hf': []})


CT2_ENTRY = {'key': 'breeze-asr-26', 'repo': 'MediaTek-Research/Breeze-ASR-26', 'revision': 'c' * 40, 'required': False,
             'download_bytes': 100, 'label': 'Breeze-ASR-26（台語語音→中文字）', 'languages': ['zh'], 'license': 'Apache-2.0'}


# ---- 模型清單 ----

def test_manifest_offers_breeze_asr_25_and_qwen_and_no_longer_26():
    catalog = asr_models.catalog()
    keys = [m['key'] for m in catalog]
    # 2026-09-20 使用者：26 移除、25 當預設候選、加入 Qwen3-ASR 比較
    # 2026-09-20 使用者：「預設 asr25 為預設第一個，再來次要是 v3」→ 清單順序 25、v3、其餘
    assert keys == ['breeze-asr-25', 'large-v3', 'qwen3-asr-1.7b']
    assert asr_models.entry('breeze-asr-26') is None
    entry25 = asr_models.entry('breeze-asr-25')
    assert entry25['repo'] == 'MediaTek-Research/Breeze-ASR-25' and entry25['revision'] == 'cffe7ccb404d025296a00758d0a33468bec3a9d0'
    assert entry25['download_bytes'] == 3092400389 and entry25['languages'] == ['zh'] and entry25['license'] == 'Apache-2.0'
    # 合約允許的值要跟清單一致
    from pydantic import ValidationError
    from app.studio.contracts import JobRequest
    # 本機清單＋遠端轉錄（只在設定了供應者時出現在清單，合約一律接受）：openrouter／elevenlabs 與「來源:模型名稱」（2026-09-21）
    for key in keys + ['openrouter', 'elevenlabs', 'openrouter:microsoft/mai-transcribe-2', 'elevenlabs:scribe_v1']:
        assert JobRequest(kind='analyze', source_id='s', asr_model=key).asr_model == key
    for key in ('breeze-asr-26', 'qwen3-asr-0.6b', 'openrouter:', 'other:model', 'openrouter:/x'):
        with pytest.raises(ValidationError):
            JobRequest(kind='analyze', source_id='s', asr_model=key)
    assert JobRequest(kind='analyze', source_id='s').asr_model is None  # 沒指定＝用設定的預設

def test_resolve_uses_the_local_converted_folder_and_explains_when_missing(tmp_path):
    assert asr_models.resolve('large-v3', tmp_path) == 'large-v3'
    assert asr_models.resolve('turbo', tmp_path) == 'turbo'
    with pytest.raises(StudioError) as caught:
        asr_models.resolve('breeze-asr-25', tmp_path)
    assert caught.value.code == 'MODEL_NOT_INSTALLED' and caught.value.status == 409
    assert 'Breeze-ASR-25' in caught.value.message and '環境' in caught.value.message
    make_ct2(tmp_path / 'ct2' / 'breeze-asr-25', with_tokenizer=False)
    with pytest.raises(StudioError):  # 少了 tokenizer.json 不算裝好（faster-whisper 會改去網路抓標準詞彙）
        asr_models.resolve('breeze-asr-25', tmp_path)
    make_ct2(tmp_path / 'ct2' / 'breeze-asr-25')
    assert asr_models.resolve('breeze-asr-25', tmp_path) == str(tmp_path / 'ct2' / 'breeze-asr-25')
    assert asr_models.languages('breeze-asr-25') == ['zh'] and asr_models.languages('large-v3') is None


# ---- 安裝：ct2 類（下載官方權重→本機轉換）----

def test_check_models_reports_ct2_items(tmp_path):
    manifest = {'ct2': [CT2_ENTRY]}
    assert model_store.check_models(manifest, tmp_path)['items'][0]['status'] == 'skipped_optional'
    item = model_store.check_models(manifest, tmp_path, include_optional=True)['items'][0]
    assert item['id'] == 'ct2:breeze-asr-26' and item['kind'] == 'ct2' and item['status'] == 'missing'
    assert item['label'] == CT2_ENTRY['label'] and item['download_bytes'] == 100 and item['required'] is False
    make_ct2(tmp_path / 'ct2' / 'breeze-asr-26', with_tokenizer=False)
    assert model_store.check_models(manifest, tmp_path, include_optional=True)['items'][0]['status'] == 'missing'
    make_ct2(tmp_path / 'ct2' / 'breeze-asr-26')
    present = model_store.check_models(manifest, tmp_path, include_optional=True)['items'][0]
    assert present['status'] == 'present' and present['bytes'] > 0


def test_convert_downloads_only_safe_official_files_converts_and_cleans_up(tmp_path):
    manifest = {'ct2': [CT2_ENTRY]}
    plan = model_store.plan_download(manifest, tmp_path, include_optional=True, only=['ct2:breeze-asr-26'])
    [item] = plan['items']
    assert item['action'] == 'convert' and item['bytes'] == 100
    calls = {}

    def fake_download(repo, local_dir, revision=None, allow_patterns=None):
        calls.update(repo=repo, revision=revision, patterns=list(allow_patterns))
        local_dir = Path(local_dir)
        local_dir.mkdir(parents=True, exist_ok=True)
        for name in ('config.json', 'generation_config.json', 'preprocessor_config.json', 'model.safetensors', 'vocab.json', 'merges.txt'):
            (local_dir / name).write_text('{}', encoding='utf-8')

    def fake_convert(source, target, quantization):
        calls['converted'] = (Path(source).name, quantization)
        target = Path(target)
        target.mkdir(parents=True)
        (target / 'model.bin').write_bytes(b'\0' * 32)
        (target / 'config.json').write_text('{}', encoding='utf-8')

    def fake_tokenizer(source, target_file):
        calls['tokenizer'] = True
        Path(target_file).write_text('{"model": {}}', encoding='utf-8')

    result = model_store.run_download(plan, ct2_download=fake_download, ct2_convert=fake_convert, ct2_tokenizer=fake_tokenizer)
    assert result['items'][0]['status'] == 'converted'
    assert calls['repo'] == 'MediaTek-Research/Breeze-ASR-26' and calls['revision'] == 'c' * 40
    assert calls['converted'][1] == 'float16' and calls['tokenizer']
    # 只下載權重（safetensors）與設定／詞彙檔；可執行程式碼的 pickle 類訓練檔一律不下載
    patterns = calls['patterns']
    assert any('safetensors' in p for p in patterns) and 'preprocessor_config.json' in patterns
    assert not any(p.endswith(('.bin', '.pkl', '.pt', '.pth', '*')) and 'safetensors' not in p for p in patterns)
    final = tmp_path / 'ct2' / 'breeze-asr-26'
    assert {'model.bin', 'config.json', 'tokenizer.json', 'preprocessor_config.json', 'STUDIO_CT2.json'} <= {p.name for p in final.iterdir()}
    provenance = json.loads((final / 'STUDIO_CT2.json').read_text(encoding='utf-8'))
    assert provenance['repo'] == 'MediaTek-Research/Breeze-ASR-26' and provenance['revision'] == 'c' * 40 and provenance['quantization'] == 'float16'
    assert provenance['license'] == 'Apache-2.0'
    # 下載的原始權重是轉換用的暫存：轉完刪掉，不留兩份
    assert not list((tmp_path / 'ct2').glob('*.staging')) and not (tmp_path / 'ct2' / '_source' / 'breeze-asr-26').exists()
    assert model_store.check_models(manifest, tmp_path, include_optional=True)['items'][0]['status'] == 'present'
    record = json.loads((tmp_path / model_store.IMPORTED).read_text(encoding='utf-8'))
    assert record['ct2']['breeze-asr-26']['revision'] == 'c' * 40


def test_failed_conversion_installs_nothing_and_keeps_the_download_for_retry(tmp_path):
    manifest = {'ct2': [CT2_ENTRY]}
    plan = model_store.plan_download(manifest, tmp_path, include_optional=True, only=['ct2:breeze-asr-26'])

    def fake_download(repo, local_dir, revision=None, allow_patterns=None):
        Path(local_dir).mkdir(parents=True, exist_ok=True)
        (Path(local_dir) / 'model.safetensors').write_bytes(b'weights')

    def broken_convert(source, target, quantization):
        Path(target).mkdir(parents=True)
        (Path(target) / 'model.bin').write_bytes(b'half')
        raise RuntimeError('out of memory')

    result = model_store.run_download(plan, ct2_download=fake_download, ct2_convert=broken_convert, ct2_tokenizer=lambda s, t: None)
    assert result['items'][0]['status'] == 'failed' and result['items'][0]['error'] == 'RuntimeError'
    assert not (tmp_path / 'ct2' / 'breeze-asr-26').exists() and not list((tmp_path / 'ct2').glob('*.staging'))
    assert (tmp_path / 'ct2' / '_source' / 'breeze-asr-26' / 'model.safetensors').exists()  # 重試不必再下載幾 GB


# ---- 精修：Breeze 只處理中文段落 ----

def cue(identity, start, end, text='文字', lang='zh'):
    return dict(id=identity, start_us=start, end_us=end, raw_text=text, accepted_text=text, lang=lang, alignment_status='segment', words=[])


def test_breeze_refines_chinese_spans_and_leaves_japanese_to_large_v3(monkeypatch, tmp_path):
    loads, calls = [], []

    def make_model(model, **kwargs):
        loads.append(model)

        def transcribe(audio, **options):
            calls.append((model, options.get('language')))
            row = SimpleNamespace(text='精修文字', start=0, end=.5, words=[], avg_logprob=-.3, compression_ratio=1)
            return iter([row]), SimpleNamespace(language=options.get('language') or 'zh', duration=len(audio) / 16000, duration_after_vad=len(audio) / 16000)
        return SimpleNamespace(transcribe=transcribe)
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=make_model))
    monkeypatch.setattr(asr, '_load_audio', lambda path: np.ones(16000 * 10, dtype=np.float32) * .1)
    monkeypatch.setattr(app_config, 'MODELS_DIR', tmp_path)
    breeze_dir = str(make_ct2(tmp_path / 'ct2' / 'breeze-asr-25'))
    source = [cue('a', 0, 1000000), cue('b', 2000000, 3000000, '日本語です', 'ja'), cue('c', 4000000, 5000000), cue('d', 6000000, 10000000)]
    result = asr.refine(source, 'audio.wav', device='cpu', model='breeze-asr-25', cue_ids=['a', 'b', 'c'], max_refine_audio_ratio=1.0, context_us=0)
    assert loads == [breeze_dir, 'large-v3']  # 同模型的段落排在一起：各載入一次
    assert (breeze_dir, 'zh') in calls and all(language == 'zh' for model, language in calls if model == breeze_dir)
    spans = {tuple(s['cue_ids']): s for s in result['routing']['selected']}
    assert spans[('a',)]['model'] == 'breeze-asr-25' and spans[('c',)]['model'] == 'breeze-asr-25'
    assert spans[('b',)]['model'] == 'large-v3' and spans[('b',)]['model_reason'] == 'language_outside_model'
    assert all(s['status'] == 'completed' for s in result['routing']['selected'])
    assert result['settings']['model'] == 'breeze-asr-25'
    refined = [c for c in result['cues'] if c.get('refinement_provenance')]
    assert {c['refinement_provenance']['model'] for c in refined} == {'breeze-asr-25', 'large-v3'}


def test_refine_with_a_missing_breeze_model_fails_before_loading_anything(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=lambda *a, **k: pytest.fail('不應載入模型')))
    monkeypatch.setattr(asr, '_load_audio', lambda path: np.ones(16000 * 2, dtype=np.float32) * .1)
    monkeypatch.setattr(app_config, 'MODELS_DIR', tmp_path)
    with pytest.raises(StudioError) as caught:
        asr.refine([cue('a', 0, 1000000)], 'audio.wav', device='cpu', model='breeze-asr-25', cue_ids=['a'], max_refine_audio_ratio=1.0, context_us=0)
    assert caught.value.code == 'MODEL_NOT_INSTALLED' and 'Breeze-ASR-25' in caught.value.message


# ---- 轉錄工作、API、CLI ----

def test_quality_transcription_uses_the_chosen_model_for_the_refine_pass(tmp_path, monkeypatch):
    from app.studio.worker import Worker
    from test_studio_reanalyze import prepare
    store, project, source, asset = prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(app_config, 'MODELS_DIR', tmp_path / 'models')  # 不受這台電腦實際裝了什麼影響
    monkeypatch.setattr(asr_models, 'REFINE_DEFAULT', 'breeze-asr-25')  # 預設規則本身另測；這裡固定一個 ct2 預設
    seen = []

    def fake_refine_align(self):
        seen.append(self.body.get('model'))
        return {'transcript_revision': self.body['transcript_revision']}
    monkeypatch.setattr(Worker, 'refine_align', fake_refine_align)
    def run(key, extra):
        job = store.submit(project, {'kind': 'analyze', 'source_id': source, 'asset_ids': [asset['id']], 'profile': 'quality',
                                     'music_policy': 'off', 'auto_align': False, **extra}, 'gpu', key=key)
        Worker(store, job).analyze()
    run('not-installed', {})  # Breeze 還沒裝：預設退回 large-v3
    make_ct2(tmp_path / 'models' / 'ct2' / 'breeze-asr-25')
    run('installed', {})  # 預設模型已安裝就用它（下載完成後自動轉錄的子工作也一樣）
    run('explicit', {'asr_model': 'large-v3'})
    assert seen == ['large-v3', 'breeze-asr-25', 'large-v3']


def test_api_rejects_breeze_before_queueing_when_missing_or_without_refine_and_lists_models(tmp_path, monkeypatch):
    from app.studio.api import create_app
    monkeypatch.setattr(app_config, 'MODELS_DIR', tmp_path / 'models')
    with TestClient(create_app(tmp_path / 'state', start_workers=False)) as c:
        c.get('/v1/session')
        project = c.post('/v1/projects', json={'name': 'Breeze'}).json()['project_id']
        source = c.post(f'/v1/projects/{project}/sources', json={'kind': 'youtube', 'url': 'https://www.youtube.com/watch?v=Yn2mE6_tMC8'}).json()['source_id']
        body = {'kind': 'analyze', 'source_id': source, 'profile': 'quality', 'asr_model': 'breeze-asr-25'}
        missing = c.post(f'/v1/projects/{project}/jobs', json=body)
        assert missing.status_code == 409 and missing.json()['error']['code'] == 'MODEL_NOT_INSTALLED'
        listed = {m['key']: m for m in c.get('/v1/capabilities').json()['asr_models']}
        assert listed['large-v3']['installed'] is True and listed['breeze-asr-25']['installed'] is False
        assert listed['breeze-asr-25']['label'] and listed['breeze-asr-25']['download_bytes'] > 1_000_000_000
        make_ct2(tmp_path / 'models' / 'ct2' / 'breeze-asr-25')
        assert c.get('/v1/capabilities').json()['asr_models'][1]['installed'] is True
        draft_only = c.post(f'/v1/projects/{project}/jobs', json={**body, 'profile': 'draft'})
        assert draft_only.status_code == 422 and draft_only.json()['error']['code'] == 'ASR_MODEL_NEEDS_REFINE'
        assert c.post(f'/v1/projects/{project}/jobs', json=body).status_code == 202
        # 模型清單的下載入口認得 ct2 類；沒確認不下載；不在清單的模型不認得
        assert c.post('/v1/models/download', json={'ids': ['ct2:breeze-asr-25'], 'confirm': False}).status_code == 422
        rejected = c.post('/v1/models/download', json={'ids': ['ct2:not-a-model'], 'confirm': True})
        assert rejected.status_code == 422 and rejected.json()['error']['code'] == 'UNKNOWN_MODEL'
        status = {m['id']: m for m in c.get('/v1/models/status').json()['items']}
        assert status['ct2:breeze-asr-25']['status'] == 'present' and 'ct2:breeze-asr-26' not in status  # 26 已移除


def test_breeze_asr_26_is_removed_everywhere_and_25_is_accepted(capsys, monkeypatch):
    import httpx
    from pydantic import ValidationError
    from app.studio.contracts import JobRequest
    from app.studio.cli import main
    with pytest.raises(ValidationError):
        JobRequest(kind='analyze', source_id='s', profile='quality', asr_model='breeze-asr-26')
    assert JobRequest(kind='analyze', source_id='s', profile='quality', asr_model='breeze-asr-25').asr_model == 'breeze-asr-25'
    monkeypatch.setenv('STUDIO_API_TOKEN', 'test-token')
    assert main(['analyze', '--project', 'p', '--source', 's', '--profile', 'quality', '--asr-model', 'breeze-asr-26', '--json']) == 2
    calls = []
    with httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(202, json={'id': 'job_1', 'status': 'queued'}))) as api:
        assert main(['analyze', '--project', 'p', '--source', 's', '--profile', 'quality', '--asr-model', 'qwen3-asr-1.7b', '--json'], client=api) == 0
    assert json.loads(calls[0].content)['asr_model'] == 'qwen3-asr-1.7b'

def test_refine_default_is_the_best_value_model_when_installed_otherwise_large_v3(tmp_path, monkeypatch):
    # 使用者 2026-09-20 最新指示：「預設 asr25 為預設第一個，再來次要是 v3」→ 預設 Breeze-ASR-25，沒安裝就退回 large-v3。
    # （CP 值數字仍記在 TEST_REPORT 第十四輪：Qwen3-ASR-1.7B 12.9 秒、large-v3 26.4 秒、Breeze-ASR-25 31.8 秒）
    from test_studio_qwen_asr import fake_overlay, fake_snapshot
    assert asr_models.REFINE_DEFAULT == 'breeze-asr-25' and asr_models.DEFAULT == 'large-v3'
    assert asr_models.entry('qwen3-asr-0.6b') is None  # 已從清單移除
    overlay = tmp_path / 'overlay'
    monkeypatch.setenv('STUDIO_QWEN_ASR_PATH', str(overlay))
    assert asr_models.default_model({}, tmp_path) == 'large-v3'  # 還沒裝：退回 large-v3，轉錄不會因預設而失敗
    qwen = asr_models.entry('qwen3-asr-1.7b')
    fake_snapshot(tmp_path, qwen['repo'], qwen['revision'])
    assert asr_models.default_model({}, tmp_path) == 'large-v3'  # 只有權重、沒有套件疊加目錄也不算裝好
    fake_overlay(overlay)
    assert asr_models.default_model({}, tmp_path) == 'large-v3'  # 裝好 Qwen 也不影響：預設是 25，25 還沒裝就退回 v3
    assert asr_models.default_model({'default_asr_model': 'qwen3-asr-1.7b'}, tmp_path) == 'qwen3-asr-1.7b'  # config.json 可指定
    make_ct2(tmp_path / 'ct2' / 'breeze-asr-25')
    assert asr_models.default_model({}, tmp_path) == 'breeze-asr-25'  # 25 裝好了就是預設
    assert asr_models.default_model({'default_asr_model': 'large-v3'}, tmp_path) == 'large-v3'  # config.json 可改回
    assert asr_models.default_model({'default_asr_model': 'no-such-model'}, tmp_path) == 'breeze-asr-25'  # 不認得的值不採用
    assert asr_models.catalog(tmp_path)[0]['key'] == 'breeze-asr-25'  # 清單第一個是預設模型

def test_api_fills_in_the_default_refine_model_and_leaves_the_draft_profile_alone(tmp_path, monkeypatch):
    from app.studio.api import create_app
    monkeypatch.setattr(app_config, 'MODELS_DIR', tmp_path / 'models')
    monkeypatch.setattr(asr_models, 'REFINE_DEFAULT', 'breeze-asr-25')
    with TestClient(create_app(tmp_path / 'state', start_workers=False)) as c:
        c.get('/v1/session')
        project = c.post('/v1/projects', json={'name': '預設模型'}).json()['project_id']
        source = c.post(f'/v1/projects/{project}/sources', json={'kind': 'youtube', 'url': 'https://www.youtube.com/watch?v=Yn2mE6_tMC8'}).json()['source_id']
        jobs = f'/v1/projects/{project}/jobs'
        assert c.get('/v1/capabilities').json()['default_asr_model'] == 'large-v3'
        before = c.post(jobs, json={'kind': 'analyze', 'source_id': source, 'profile': 'quality'})
        assert before.status_code == 202 and before.json()['body']['asr_model'] == 'large-v3'
        make_ct2(tmp_path / 'models' / 'ct2' / 'breeze-asr-25')
        assert c.get('/v1/capabilities').json()['default_asr_model'] == 'breeze-asr-25'
        after = c.post(jobs, json={'kind': 'analyze', 'source_id': source, 'profile': 'balanced'})
        assert after.status_code == 202 and after.json()['body']['asr_model'] == 'breeze-asr-25'  # 工作紀錄寫明實際用的模型
        draft = c.post(jobs, json={'kind': 'analyze', 'source_id': source, 'profile': 'draft'})
        assert draft.status_code == 202 and draft.json()['body'].get('asr_model') is None  # 快速草稿不做精修：不套預設、不擋


def test_cli_analyze_accepts_the_asr_model(capsys, monkeypatch):
    import httpx
    from app.studio.cli import main
    monkeypatch.setenv('STUDIO_API_TOKEN', 'test-token')
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(202, json={'id': 'job_1', 'status': 'queued'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as api:
        assert main(['analyze', '--project', 'p', '--source', 's', '--profile', 'quality', '--asr-model', 'breeze-asr-25', '--json'], client=api) == 0
    assert json.loads(calls[0].content)['asr_model'] == 'breeze-asr-25'


def test_real_converter_and_tokenizer_produce_a_model_faster_whisper_loads_offline(tmp_path, monkeypatch):
    """真的跑 CTranslate2 轉換器、tokenizer 產生與 faster-whisper 載入（只有「下載」用替身）。
    來源是本機做的迷你 Whisper（隨機權重、80 mel、標準詞彙、沒有 tokenizer.json＝模擬 Breeze-26），不連網。"""
    transformers = pytest.importorskip('transformers')
    pytest.importorskip('ctranslate2')
    faster_whisper = pytest.importorskip('faster_whisper')
    vocab_files = sorted((Path(app_config.MODELS_DIR) / 'hf' / 'models--mobiuslabsgmbh--faster-whisper-large-v3-turbo' / 'snapshots').glob('*/tokenizer.json'))
    if not vocab_files:
        pytest.skip('本機沒有 Whisper 詞彙檔（models/hf turbo）')
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    tiny = tmp_path / 'tiny_source'
    tokenizer = transformers.WhisperTokenizerFast(tokenizer_file=str(vocab_files[0]))
    tokenizer.save_pretrained(tiny)
    (tiny / 'tokenizer.json').unlink()  # Breeze-26 的 repo 沒有 tokenizer.json：要靠 vocab.json＋merges.txt 在本機產生
    transformers.WhisperFeatureExtractor(feature_size=80).save_pretrained(tiny)
    config = transformers.WhisperConfig(vocab_size=len(tokenizer), num_mel_bins=80, d_model=64, encoder_layers=2, decoder_layers=2,
        encoder_attention_heads=2, decoder_attention_heads=2, encoder_ffn_dim=128, decoder_ffn_dim=128,
        decoder_start_token_id=50258, pad_token_id=50257, bos_token_id=50257, eos_token_id=50257)
    transformers.WhisperForConditionalGeneration(config).save_pretrained(tiny, safe_serialization=True)
    import shutil

    def local_download(repo, local_dir, revision=None, allow_patterns=None):
        import fnmatch
        Path(local_dir).mkdir(parents=True, exist_ok=True)
        for item in tiny.iterdir():
            if any(fnmatch.fnmatch(item.name, pattern) for pattern in allow_patterns):
                shutil.copy2(item, Path(local_dir) / item.name)
    manifest = {'ct2': [CT2_ENTRY]}
    use_manifest(monkeypatch, CT2_ENTRY)  # 26 已不在真實清單：用測試清單
    plan = model_store.plan_download(manifest, tmp_path / 'models', include_optional=True, only=['ct2:breeze-asr-26'])
    result = model_store.run_download(plan, ct2_download=local_download)  # 轉換與 tokenizer 用真的預設實作
    assert result['items'][0]['status'] == 'converted', result['items'][0]
    final = tmp_path / 'models' / 'ct2' / 'breeze-asr-26'
    assert json.loads((final / 'config.json').read_text(encoding='utf-8')).get('alignment_heads')
    assert asr_models.resolve('breeze-asr-26', tmp_path / 'models') == str(final)
    model = faster_whisper.WhisperModel(str(final), device='cpu', compute_type='int8')
    assert model.hf_tokenizer.get_vocab_size() == len(tokenizer) and model.feature_extractor.mel_filters.shape[0] == 80
    segments, info = model.transcribe(np.zeros(16000, dtype=np.float32), language='zh', beam_size=1, without_timestamps=True,
                                      max_new_tokens=8, condition_on_previous_text=False)
    list(segments)  # 隨機權重只驗證能跑，不驗內容
    assert info.language == 'zh'


# ---- 2026-09-19 真跑發現：Breeze-ASR-26 不會預測時間戳 ----
# 同一段音訊交給 faster-whisper：Breeze 每 30 秒只回一大段、時間錯（例如 2.90–3.32 秒卻含 47 秒的話）；
# 全流程 68 句中 15 句短於 0.2 秒、最高每秒 1775 字，並出現「二二三開曖」重複迴圈。
# 修正：沒有時間戳的模型只換文字——每句草稿只解碼那一句自己的音訊（不加前後文，避免吃到鄰句），
# 時間沿用草稿句界、逐詞時間交給之後的 wav2vec2 對齊；解碼長度依句長設上限，輸出過長（重複迴圈）就不採用、保留草稿。

def test_timestamp_capability_comes_from_the_manifest(monkeypatch):
    assert asr_models.timestamps('breeze-asr-25') is True and asr_models.timestamps('large-v3') is True
    assert asr_models.timestamps('qwen3-asr-1.7b') is False and asr_models.timestamps('openrouter') is False
    use_manifest(monkeypatch, NOTIME)
    assert asr_models.timestamps('notime-test') is False

def _fake_engine(monkeypatch, tmp_path, texts):
    calls = []

    def make_model(model, **kwargs):
        def transcribe(audio, **options):
            calls.append(dict(model=model, samples=len(audio), **options))
            text = texts(model, len(audio) / 16000)
            # Breeze 故意給錯的時間（真跑見過）；large-v3 給正常時間（段落內 0.3–1.3 秒＝句子本身）
            start, end = (0, .04) if 'notime' in model else (.3, 1.3)
            row = SimpleNamespace(text=text, start=start, end=end, words=[], avg_logprob=-.3, compression_ratio=1.2)
            return iter([row]), SimpleNamespace(language=options.get('language') or 'zh', duration=len(audio) / 16000, duration_after_vad=len(audio) / 16000)
        return SimpleNamespace(transcribe=transcribe)
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=make_model))
    monkeypatch.setattr(asr, '_load_audio', lambda path: np.ones(16000 * 10, dtype=np.float32) * .1)
    monkeypatch.setattr(app_config, 'MODELS_DIR', tmp_path)
    use_manifest(monkeypatch, NOTIME)
    return calls, str(make_ct2(tmp_path / 'ct2' / 'notime-test'))


def test_model_without_timestamps_only_replaces_text_and_keeps_draft_timing(monkeypatch, tmp_path):
    calls, breeze_dir = _fake_engine(monkeypatch, tmp_path, lambda model, seconds: '替換文字' if 'notime' in model else 'にほんご')
    source = [cue('a', 1000000, 2500000, '草稿文字'), cue('b', 3000000, 4000000, '日本語', 'ja'), cue('c', 6000000, 9000000)]
    result = asr.refine(source, 'audio.wav', device='cpu', model='notime-test', cue_ids=['a', 'b'], max_refine_audio_ratio=1.0, context_us=300000)
    a = next(c for c in result['cues'] if c.get('origin_cue_ids') == ['a'])
    assert (a['start_us'], a['end_us']) == (1000000, 2500000)  # 草稿句界，不是模型給的 0.04 秒
    assert a['raw_text'] == '替換文字' and a['words'] == [] and a['lang'] == 'zh'
    assert a['refinement_provenance']['model'] == 'notime-test' and a['refinement_provenance']['timing'] == 'draft_cue'
    breeze_calls = [c for c in calls if c['model'] == breeze_dir]
    assert len(breeze_calls) == 1
    call = breeze_calls[0]
    assert call['samples'] == 24000  # 只有那一句自己的 1.5 秒，不含前後 0.3 秒
    assert call['without_timestamps'] is True and call['word_timestamps'] is False and call['vad_filter'] is False
    assert call['language'] == 'zh' and 0 < call['max_new_tokens'] <= math.ceil(1.5 * 15) + 10  # 上限隨句長：每秒 15 個 token＋10
    # 日文句照舊走 large-v3（有時間戳，用前後文補邊再裁回句界）
    b_calls = [c for c in calls if c['model'] == 'large-v3']
    assert len(b_calls) == 1 and b_calls[0]['samples'] == 16000 * 16 // 10 and b_calls[0].get('without_timestamps') is not True
    assert all(s['status'] == 'completed' for s in result['routing']['selected'])


def test_runaway_output_from_a_model_without_timestamps_is_rejected_and_the_draft_is_kept(monkeypatch, tmp_path):
    _fake_engine(monkeypatch, tmp_path, lambda model, seconds: '二二三開曖 ' * 30)
    source = [cue('a', 1000000, 1500000, '草稿文字')]
    result = asr.refine(source, 'audio.wav', device='cpu', model='notime-test', cue_ids=['a'], max_refine_audio_ratio=1.0, context_us=0)
    [span] = result['routing']['selected']
    assert span['status'] == 'failed' and span['error']['code'] == 'REFINEMENT_OUTPUT_REJECTED'
    assert result['cues'] == source and 'refinement_failed_original_preserved' in result['warnings']
