"""CLI 供應者金鑰命令（M0-7）：金鑰只能從 stdin 或環境變數讀取，不在命令列引數。"""
import io
import json

import httpx

from app.studio.cli import main


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_provider_secret_set_reads_stdin_and_puts(monkeypatch, capsys):
    calls = []
    def handler(request):
        calls.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={'id': 'api-deepseek', 'secret_configured': True})
    monkeypatch.setattr('sys.stdin', io.StringIO('sk-from-stdin\n'))
    with client(handler) as api:
        assert main(['provider', 'secret-set', 'api-deepseek', '--json'], client=api) == 0
    assert calls == [('PUT', '/v1/providers/api-deepseek/secret', {'secret': 'sk-from-stdin'})]
    out = capsys.readouterr().out
    assert 'sk-from-stdin' not in out and json.loads(out.strip().splitlines()[-1])['secret_configured'] is True


def test_provider_secret_set_prefers_env_and_rejects_empty(monkeypatch, capsys):
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'id': 'api-openrouter', 'secret_configured': True})
    monkeypatch.setenv('STUDIO_PROVIDER_SECRET', 'sk-from-env')
    monkeypatch.setattr('sys.stdin', io.StringIO(''))
    with client(handler) as api:
        assert main(['provider', 'secret-set', 'api-openrouter'], client=api) == 0
    assert calls == [{'secret': 'sk-from-env'}]
    monkeypatch.delenv('STUDIO_PROVIDER_SECRET')
    monkeypatch.setattr('sys.stdin', io.StringIO('   \n'))
    with client(handler) as api:
        assert main(['provider', 'secret-set', 'api-openrouter'], client=api) == 2
    assert len(calls) == 1


def test_provider_secret_delete(capsys):
    def handler(request):
        assert request.method == 'DELETE' and request.url.path == '/v1/providers/api-deepseek/secret'
        return httpx.Response(200, json={'id': 'api-deepseek', 'secret_configured': False})
    with client(handler) as api:
        assert main(['provider', 'secret-delete', 'api-deepseek', '--json'], client=api) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])['secret_configured'] is False
