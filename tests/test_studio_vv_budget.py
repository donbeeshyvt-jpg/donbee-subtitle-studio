"""大型模型啟動前的顯示卡記憶體閘門。"""
from types import SimpleNamespace
from unittest.mock import Mock
import sys
import json
from pathlib import Path
import pytest
from app.vv_worker import require_cuda_budget
from app.vv_worker import main
from app.vv_worker import error_payload

@pytest.mark.parametrize('free,allowed', [(11499,False),(11500,True),(12000,True)])
def test_cuda_budget_boundary(free, allowed):
    cuda=SimpleNamespace(is_available=lambda:True, mem_get_info=lambda:(free*1024**2,12288*1024**2))
    if allowed:
        assert require_cuda_budget(cuda)['free_mib']==free
    else:
        with pytest.raises(RuntimeError,match='GPU_MEMORY_INSUFFICIENT'):
            require_cuda_budget(cuda)

def test_no_cuda_does_not_query_memory():
    query=Mock()
    with pytest.raises(RuntimeError,match='CUDA_UNAVAILABLE'):
        require_cuda_budget(SimpleNamespace(is_available=lambda:False,mem_get_info=query))
    query.assert_not_called()

def test_unknown_memory_is_not_treated_as_available():
    with pytest.raises(RuntimeError,match='GPU_MEMORY_UNAVAILABLE'):
        require_cuda_budget(SimpleNamespace(is_available=lambda:True,mem_get_info=Mock(side_effect=OSError('driver'))))

def test_worker_refuses_before_model_load(monkeypatch):
    load=Mock()
    monkeypatch.setitem(sys.modules,'torch',SimpleNamespace(cuda=SimpleNamespace(
        is_available=lambda:True,mem_get_info=lambda:(1024**3,12*1024**3))))
    monkeypatch.setitem(sys.modules,'vibevoice_asr_to_srt',SimpleNamespace(load_model=load,transcribe=Mock()))
    monkeypatch.setattr(sys,'argv',['vv_worker','input.wav','output.json'])
    with pytest.raises(RuntimeError,match='GPU_MEMORY_INSUFFICIENT'):
        main()
    load.assert_not_called()

def test_resource_error_is_structured_and_unknown_error_is_redacted():
    assert error_payload(RuntimeError('GPU_MEMORY_INSUFFICIENT: detail'))['code']=='GPU_MEMORY_INSUFFICIENT'
    assert 'secret' not in str(error_payload(RuntimeError('secret path token')))

def test_parent_preserves_child_resource_failure(monkeypatch):
    import numpy as np
    from app.studio.asr import _vibevoice
    from app.studio.store import StudioError
    def process(command, *args):
        Path(command[4]).write_text(json.dumps({'error': error_payload(RuntimeError('GPU_MEMORY_INSUFFICIENT'))}), encoding='utf-8')
        return SimpleNamespace(wait=lambda timeout: 1)
    monkeypatch.setattr('app.studio.process.ManagedProcess', process)
    with pytest.raises(StudioError) as caught:
        _vibevoice(np.zeros(1600), 10)
    assert caught.value.code=='GPU_MEMORY_INSUFFICIENT'
    assert '記憶體不足' in str(caught.value)
