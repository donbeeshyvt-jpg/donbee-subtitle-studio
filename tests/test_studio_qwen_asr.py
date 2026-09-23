"""2026-09-20 使用者：「可以用 Qwen3-ASR 試看看，可以下載使用」，並加入速度／效能比對。

Qwen3-ASR（Qwen/Qwen3-ASR-1.7B，Apache-2.0；0.6B 於 2026-09-20 依使用者要求移除）以官方 qwen-asr 套件（transformers 後端）執行：
- 套件裝在專案內獨立的 `.venv-qwen-asr`（--no-deps），借用主環境的 torch 與 transformers 4.57.6（qwen-asr 正好鎖這個版本），主環境不動；
- 在子程序執行（app.studio.qwen_worker），一次載入、整批轉錄所有句子；
- 精修只換文字（時間沿用草稿句界、逐詞時間交給 wav2vec2），和 Breeze-ASR-26／OpenRouter 同一條路；各語言都處理。
模型與套件一律用替身，不載入真模型。"""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app import config as app_config
from app.studio import asr, asr_models
from app.studio.store import StudioError


def fake_snapshot(models_dir, repo, revision):
    snapshot = models_dir / 'hf' / ('models--' + repo.replace('/', '--')) / 'snapshots' / revision
    snapshot.mkdir(parents=True)
    (snapshot / 'config.json').write_text('{}', encoding='utf-8')
    (snapshot / 'model.safetensors').write_bytes(b'\0' * 16)
    return snapshot


def fake_overlay(root):
    (root / 'qwen_asr').mkdir(parents=True)
    (root / 'qwen_asr' / '__init__.py').write_text('', encoding='utf-8')
    return root


def test_manifest_offers_both_qwen3_asr_sizes_pinned_to_official_revisions():
    q17 = asr_models.entry('qwen3-asr-1.7b')
    assert q17['repo'] == 'Qwen/Qwen3-ASR-1.7B' and q17['revision'] == '7278e1e70fe206f11671096ffdd38061171dd6e5'
    assert asr_models.entry('qwen3-asr-0.6b') is None  # 2026-09-20 使用者要求移除
    for entry in (q17,):
        assert entry['runner'] == 'qwen' and entry['license'] == 'Apache-2.0' and entry['languages'] is None and entry['download_id'] == 'hf:' + entry['repo']
    assert asr_models.timestamps('qwen3-asr-1.7b') is False and asr_models.runner('qwen3-asr-1.7b') == 'qwen'
    assert asr_models.runner('breeze-asr-25') == 'ct2' and asr_models.runner('large-v3') == 'builtin' and asr_models.runner('openrouter') == 'remote'
    from app.studio.contracts import JobRequest
    assert JobRequest(kind='analyze', source_id='s', profile='quality', asr_model='qwen3-asr-1.7b').asr_model == 'qwen3-asr-1.7b'


def test_qwen_is_installed_only_with_both_the_weights_and_the_package_overlay(tmp_path, monkeypatch):
    overlay = tmp_path / 'overlay'
    monkeypatch.setenv('STUDIO_QWEN_ASR_PATH', str(overlay))
    entry = asr_models.entry('qwen3-asr-1.7b')
    assert not asr_models.installed('qwen3-asr-1.7b', tmp_path)
    snapshot = fake_snapshot(tmp_path, entry['repo'], entry['revision'])
    assert not asr_models.installed('qwen3-asr-1.7b', tmp_path)  # 有權重、沒套件
    with pytest.raises(StudioError) as caught:
        asr_models.resolve('qwen3-asr-1.7b', tmp_path)
    assert caught.value.code == 'MODEL_NOT_INSTALLED' and 'qwen-asr' in caught.value.message
    fake_overlay(overlay)
    assert asr_models.installed('qwen3-asr-1.7b', tmp_path) and asr_models.resolve('qwen3-asr-1.7b', tmp_path) == str(snapshot)
    listed = {m['key']: m for m in asr_models.catalog(tmp_path)}
    assert listed['qwen3-asr-1.7b']['installed'] is True and listed['breeze-asr-25']['installed'] is False
    assert listed['qwen3-asr-1.7b']['download_id'] == 'hf:Qwen/Qwen3-ASR-1.7B'


def test_refine_with_qwen_transcribes_all_sentences_in_one_batch_and_keeps_draft_timing(tmp_path, monkeypatch):
    overlay = fake_overlay(tmp_path / 'overlay')
    monkeypatch.setenv('STUDIO_QWEN_ASR_PATH', str(overlay))
    monkeypatch.setattr(app_config, 'MODELS_DIR', tmp_path)
    entry = asr_models.entry('qwen3-asr-1.7b')
    snapshot = fake_snapshot(tmp_path, entry['repo'], entry['revision'])
    batches = []

    def fake_batch(pieces, *, model_dir, languages, contexts, device, timeout_sec):
        batches.append(dict(sizes=[len(p) for p in pieces], model_dir=model_dir, languages=languages, contexts=contexts))
        return [dict(text='千問文字' if lang == 'Chinese' else 'qwen text', language=lang) for lang in languages]
    monkeypatch.setattr(asr, '_qwen_batch', fake_batch)
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=lambda *a, **k: pytest.fail('不應載入 faster-whisper')))
    monkeypatch.setattr(asr, '_load_audio', lambda path: np.ones(16000 * 10, dtype=np.float32) * .1)
    cue = lambda i, s, e, t, lang='zh': dict(id=i, start_us=s, end_us=e, raw_text=t, accepted_text=t, lang=lang, alignment_status='segment', words=[])
    source = [cue('a', 1000000, 2500000, '草稿'), cue('b', 3000000, 4000000, 'draft', 'en'), cue('c', 5000000, 6000000, '第三句')]
    result = asr.refine(source, 'audio.wav', device='cpu', model='qwen3-asr-1.7b', cue_ids=['a', 'b', 'c'], max_refine_audio_ratio=1.0, context_us=300000,
                        hints='彭彭, 斯斯, PICO PARK')
    assert len(batches) == 1  # 一次載入、整批轉錄
    [batch] = batches
    assert batch['sizes'] == [24000, 16000, 16000] and batch['model_dir'] == str(snapshot)  # 每句只送自己的音訊（不加前後文）
    assert batch['languages'] == ['Chinese', 'English', 'Chinese'] and batch['contexts'] == ['彭彭, 斯斯, PICO PARK'] * 3
    a = next(c for c in result['cues'] if c.get('origin_cue_ids') == ['a'])
    assert (a['start_us'], a['end_us'], a['raw_text']) == (1000000, 2500000, '千問文字')
    assert a['refinement_provenance']['model'] == 'qwen3-asr-1.7b' and a['refinement_provenance']['timing'] == 'draft_cue'
    assert all(s['status'] == 'completed' and s['model'] == 'qwen3-asr-1.7b' for s in result['routing']['selected'])


def test_a_failed_qwen_batch_keeps_every_draft_sentence(tmp_path, monkeypatch):
    overlay = fake_overlay(tmp_path / 'overlay')
    monkeypatch.setenv('STUDIO_QWEN_ASR_PATH', str(overlay))
    monkeypatch.setattr(app_config, 'MODELS_DIR', tmp_path)
    entry = asr_models.entry('qwen3-asr-1.7b')
    fake_snapshot(tmp_path, entry['repo'], entry['revision'])

    def broken(pieces, **kwargs):
        raise StudioError('QWEN_ASR_FAILED', 'CUDA out of memory', 409)
    monkeypatch.setattr(asr, '_qwen_batch', broken)
    monkeypatch.setattr(asr, '_load_audio', lambda path: np.ones(16000 * 4, dtype=np.float32) * .1)
    source = [dict(id='a', start_us=0, end_us=1000000, raw_text='草稿', accepted_text='草稿', lang='zh', alignment_status='segment', words=[])]
    result = asr.refine(source, 'audio.wav', device='cpu', model='qwen3-asr-1.7b', cue_ids=['a'], max_refine_audio_ratio=1.0, context_us=0)
    [span] = result['routing']['selected']
    assert span['status'] == 'failed' and span['error']['code'] == 'QWEN_ASR_FAILED'
    assert result['cues'] == source and 'refinement_failed_original_preserved' in result['warnings']


def test_qwen_worker_process_writes_texts_and_timing(tmp_path, monkeypatch):
    # 子程序介面：輸入 JSON（模型、每句 wav 路徑、語言、提示）→ 輸出 JSON；qwen_asr 用替身模組
    fake = tmp_path / 'fake_pkgs' / 'qwen_asr'
    fake.mkdir(parents=True)
    (fake / '__init__.py').write_text(
        "class _R:\n    def __init__(self, text, language): self.text, self.language = text, language\n"
        "class Qwen3ASRModel:\n"
        "    @classmethod\n"
        "    def from_pretrained(cls, path, **kwargs):\n        m = cls(); m.kwargs = kwargs; return m\n"
        "    def transcribe(self, audio, context='', language=None):\n"
        "        return [_R('句%d:%s:%s' % (i, (language or [None])[i], (context or [''])[i]), (language or ['Chinese'])[i]) for i in range(len(audio))]\n",
        encoding='utf-8')
    wavs = []
    for index in range(2):
        path = tmp_path / f'{index}.wav'
        asr._write_wav(path, np.zeros(8000, dtype=np.float32))
        wavs.append(str(path))
    spec = tmp_path / 'in.json'
    spec.write_text(json.dumps({'model': str(tmp_path), 'device': 'cpu', 'items': [
        {'path': wavs[0], 'language': 'Chinese', 'context': '彭彭'}, {'path': wavs[1], 'language': None, 'context': ''}]}, ensure_ascii=False), encoding='utf-8')
    out = tmp_path / 'out.json'
    env = {**__import__('os').environ, 'PYTHONPATH': str(tmp_path / 'fake_pkgs') + __import__('os').pathsep + str(Path(__file__).resolve().parents[1] / 'src')}
    done = subprocess.run([sys.executable, '-m', 'app.studio.qwen_worker', '--input', str(spec), '--output', str(out)], env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    result = json.loads(out.read_text(encoding='utf-8'))
    assert result['ok'] is True and [r['text'] for r in result['results']] == ['句0:Chinese:彭彭', '句1:None:']
    assert result['load_sec'] >= 0 and result['infer_sec'] >= 0
