"""CLI 與 API 同一套模型狀態／下載契約（P3-c）：models status、models download 需 --confirm。"""
import json

import httpx
import pytest

from app.studio.cli import main


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_models_status_prints_api_result(capsys):
    def handler(request):
        assert request.method == 'GET' and request.url.path.endswith('/models/status')
        return httpx.Response(200, json={'items': [{'id': 'hf:MIT/ast', 'status': 'present', 'required': True}], 'required_missing': [], 'offline_ready': True, 'download': None})
    with client(handler) as api:
        assert main(['models', 'status', '--json'], client=api) == 0
    assert json.loads(capsys.readouterr().out)['offline_ready'] is True


def test_models_download_requires_confirm_flag_and_posts_ids(capsys):
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(202, json={'status': 'running', 'ids': ['hf:microsoft/VibeVoice-ASR'], 'plan': []})
    with client(handler) as api:
        assert main(['models', 'download', '--ids', 'hf:microsoft/VibeVoice-ASR', '--json'], client=api) == 2, '未加 --confirm 不得送出'
        assert calls == []
        assert main(['models', 'download', '--ids', 'hf:microsoft/VibeVoice-ASR', '--confirm', '--json'], client=api) == 0
    assert calls == [{'ids': ['hf:microsoft/VibeVoice-ASR'], 'confirm': True}]
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])['status'] == 'running'


@pytest.mark.parametrize('last,expected', [('downloaded', 0), ('failed', 3), ('manual_required', 3)])
def test_models_wait_polls_progress_and_checks_each_result(capsys, last, expected):
    polls = []
    def handler(request):
        if request.method == 'POST':
            return httpx.Response(202, json={'status': 'running', 'ids': ['hf:test'], 'download_id': 'dl1'})
        polls.append(request.url.path)
        state = dict(download_id='dl1', status='running', events=[{'id': 'hf:test', 'stage': 'progress', 'files_done': 1, 'files_total': 3}])
        if len(polls) > 1:
            state.update(status='done', results=[{'id': 'hf:test', 'status': last}])
        return httpx.Response(200, json={'download': state})
    with client(handler) as api:
        assert main(['models', 'download', '--ids', 'hf:test', '--confirm', '--wait', '--poll-interval', '0.001', '--json'], client=api) == expected
    captured = capsys.readouterr()
    assert len(polls) == 2
    assert json.loads(captured.out)['status'] == 'done'
    assert 'files_done' in captured.err and 'hf:test' in captured.err


def test_models_wait_timeout_does_not_cancel_server(capsys, monkeypatch):
    from app.studio import cli
    ticks = iter([0, 2])
    monkeypatch.setattr(cli.time, 'monotonic', lambda: next(ticks))
    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(202, json={'status': 'running', 'ids': ['hf:test']})
    with client(handler) as api:
        assert main(['models', 'download', '--ids', 'hf:test', '--confirm', '--wait', '--wait-timeout', '1', '--json'], client=api) == 5
    assert calls == ['POST']
    assert 'WAIT_TIMEOUT' in capsys.readouterr().out
