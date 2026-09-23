"""VibeVoice 有界批次共用模型。"""
import wave
from unittest.mock import Mock
import pytest
from app.vv_worker import transcribe_batch

def wav(tmp_path, seconds):
    path=tmp_path/f'{seconds}.wav'
    with wave.open(str(path),'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
        stream.writeframes(b'\0\0'*int(seconds*16000))
    return str(path)

def test_batch_loads_model_once_and_keeps_order(tmp_path):
    load=Mock(return_value=('model','processor'))
    transcribe=Mock(side_effect=[('first',[]),('second',[])])
    rows=[{'wav':wav(tmp_path,1),'max_new_tokens':512},{'wav':wav(tmp_path,2),'max_new_tokens':512}]
    result=transcribe_batch(rows,load,transcribe)
    load.assert_called_once()
    assert [r['raw'] for r in result]==['first','second']
    assert [call.args[2] for call in transcribe.call_args_list]==[r['wav'] for r in rows]

@pytest.mark.parametrize('case',['empty','count','duration','tokens'])
def test_invalid_batch_does_not_load(tmp_path,case):
    row={'wav':wav(tmp_path,181 if case=='duration' else 1),'max_new_tokens':0 if case=='tokens' else 512}
    rows=[] if case=='empty' else [row]*(9 if case=='count' else 1)
    load=Mock()
    with pytest.raises(ValueError): transcribe_batch(rows,load,Mock())
    load.assert_not_called()
