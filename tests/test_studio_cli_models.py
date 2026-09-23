"""CLI 與 API 同一套模型狀態／下載契約（P3-c）：models status、models download 需 --confirm。"""
import json

import httpx

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
