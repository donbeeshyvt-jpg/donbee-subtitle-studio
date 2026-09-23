"""同一資料目錄只能有一個服務：第二個要以清楚的中文訊息拒絕，不能是 PermissionError traceback，也不能開瀏覽器。"""
import sys
from types import SimpleNamespace

import pytest

from app.studio import cli
from app.studio.coordinator import Coordinator
from app.studio.store import Store, StudioError


def held(tmp_path):
    store = Store(tmp_path)
    first = Coordinator(store)
    first.start()
    return store, first


def release(first):
    first.stopping.set()
    first.lock.close()


def test_second_coordinator_on_same_data_dir_fails_clearly(tmp_path):
    store, first = held(tmp_path)
    try:
        with pytest.raises(StudioError) as error:
            Coordinator(store).start()
        assert error.value.code == 'SERVICE_ALREADY_RUNNING'
        assert '資料目錄' in str(error.value) and 'STUDIO_DATA_DIR' in str(error.value)
        assert Coordinator.available(tmp_path) is False
    finally:
        release(first)
    assert Coordinator.available(tmp_path) is True


def test_serve_refuses_before_starting_uvicorn_or_browser(tmp_path, monkeypatch, capsys):
    store, first = held(tmp_path)
    opened = []
    monkeypatch.setattr(cli.webbrowser, 'open', lambda url: opened.append(url))
    monkeypatch.setitem(sys.modules, 'uvicorn', SimpleNamespace(run=lambda *args, **kwargs: pytest.fail('鎖被占用時不得啟動 uvicorn')))
    try:
        code = cli.main(['serve', '--data-dir', str(tmp_path), '--port', '8799', '--open-browser'])
    finally:
        release(first)
    assert code == 3
    out = capsys.readouterr().out
    assert 'SERVICE_ALREADY_RUNNING' in out and '資料目錄' in out
    assert opened == []
