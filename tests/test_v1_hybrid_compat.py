"""M0-5：v1 asr_hybrid 與 v2 共用 vv_worker 的門檻／錯誤 JSON 後，退回行為與訊息不變（不載模型的契約測試）。"""
import json
import math
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app import asr_hybrid, vv_worker


def fake_cuda(free_mib, total_mib=16000):
    return SimpleNamespace(is_available=lambda: True, mem_get_info=lambda: (free_mib * 1024**2, total_mib * 1024**2))


def test_v1_worker_budget_threshold_is_shared_11500_mib():
    assert vv_worker.require_cuda_budget(fake_cuda(11500))['required_mib'] == 11500
    with pytest.raises(RuntimeError, match='GPU_MEMORY_INSUFFICIENT: required=11500 MiB, free=11499 MiB'):
        vv_worker.require_cuda_budget(fake_cuda(11499))


def test_v1_falls_back_when_worker_reports_structured_budget_error(tmp_path, monkeypatch, capsys):
    """子程序寫出結構化錯誤 JSON 並以非零碼結束：v1 退回 WhisperX（回 None）且訊息不變。"""
    wav = tmp_path / 'clip.wav'
    wav.write_bytes(b'RIFF')
    def run(cmd, timeout, check, env, cwd):
        assert cmd[:3] == [sys.executable, '-m', 'app.vv_worker'] and cmd[3] == str(wav)
        assert env['PYTHONPATH'].endswith('src') and cwd.endswith('src')
        payload = {'error': vv_worker.error_payload(RuntimeError('GPU_MEMORY_INSUFFICIENT: required=11500 MiB, free=8000 MiB'))}
        (tmp_path / 'clip.wav.vv.json').write_text(json.dumps(payload), encoding='utf-8')
        raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(asr_hybrid.subprocess, 'run', run)
    assert asr_hybrid._vibevoice_text(str(wav), 3.0, 120) is None
    assert '退回 WhisperX' in capsys.readouterr().out


def test_v1_falls_back_on_timeout_and_reads_segments_on_success(tmp_path, monkeypatch, capsys):
    wav = tmp_path / 'clip.wav'
    wav.write_bytes(b'RIFF')
    def timeout(cmd, timeout, check, env, cwd):
        raise subprocess.TimeoutExpired(cmd, timeout)
    monkeypatch.setattr(asr_hybrid.subprocess, 'run', timeout)
    assert asr_hybrid._vibevoice_text(str(wav), 3.0, 7) is None
    assert '超時 7s' in capsys.readouterr().out
    def success(cmd, timeout, check, env, cwd):
        (tmp_path / 'clip.wav.vv.json').write_text(json.dumps({'segments': [{'start': 0, 'end': 1, 'text': '你好'}]}), encoding='utf-8')
    monkeypatch.setattr(asr_hybrid.subprocess, 'run', success)
    assert asr_hybrid._vibevoice_text(str(wav), 3.0, 120) == [{'start': 0, 'end': 1, 'text': '你好'}]


def test_v1_max_new_tokens_scale_with_duration_and_worker_caps_at_8192(tmp_path, monkeypatch):
    seen = {}
    def run(cmd, timeout, check, env, cwd):
        seen['max_new'] = int(cmd[5])
    monkeypatch.setattr(asr_hybrid.subprocess, 'run', run)
    wav = tmp_path / 'clip.wav'
    wav.write_bytes(b'RIFF')
    asr_hybrid._vibevoice_text(str(wav), 1.0, 120)
    assert seen['max_new'] == 512
    asr_hybrid._vibevoice_text(str(wav), 600.0, 120)
    assert seen['max_new'] == max(512, int(math.ceil(600 * asr_hybrid.VV_TOKENS_PER_SEC * 1.5)))
    assert vv_worker.error_payload(RuntimeError('CUDA_UNAVAILABLE'))['code'] == 'CUDA_UNAVAILABLE'
