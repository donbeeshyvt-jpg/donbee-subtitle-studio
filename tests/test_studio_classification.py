"""分類契約測試；模型替身不代表 AST 的真實辨識品質。"""
from copy import deepcopy
from types import SimpleNamespace
import wave

import numpy as np
import pytest

from app.studio import classification as c


def wav(tmp_path, seconds=1, silent=False):
    path = tmp_path / 'sample.wav'
    frames = np.zeros(round(seconds * 16000), dtype=np.int16) if silent else (
        np.sin(np.arange(round(seconds * 16000)) * 2 * np.pi * 440 / 16000) * 8000).astype(np.int16)
    with wave.open(str(path), 'wb') as stream:
        stream.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        stream.writeframes(frames.tobytes())
    return path


@pytest.mark.parametrize('speech,music,label', [(.8,.1,'speech'),(.7,.9,'mixed'),(.12,.95,'mixed'),
    (.02,.96,'music'),(.02,.7,'uncertain'),(.15,.1,'uncertain'),(.01,.01,'uncertain')])
def test_conservative_multilabel_decision(speech, music, label):
    assert c.classify_probabilities([speech,music], {0:'Speech',1:'Music'})['label'] == label


def test_overlapping_family_classes_use_max_not_sum():
    result = c.classify_probabilities([.2,.2,.2,.01], {0:'Music',1:'Musical instrument',2:'Singing',3:'Speech'})
    assert result['music_score'] == .2
    assert result['label'] == 'uncertain'


@pytest.mark.parametrize('probabilities', [[float('nan'),.3], [1.1,.4],[-.1,.4],[]])
def test_reject_invalid_model_probabilities(probabilities):
    with pytest.raises(ValueError): c.classify_probabilities(probabilities,{0:'Speech',1:'Music'})


def test_batch_count_and_decode_once(tmp_path,monkeypatch):
    path=wav(tmp_path,5.5)
    decode=c._decode_audio
    loads=[]
    def track(path):
        loads.append(path)
        return decode(path)
    monkeypatch.setattr(c,'_decode_audio',track)
    monkeypatch.setattr(c,'_load_model',lambda device: ('extractor','model',{0:'Speech',1:'Music'},{'revision':'mock'}))
    batches=[]
    def predict(extractor,model,windows,sample_rate,device):
        batches.append([len(w) for w in windows])
        return np.tile([.8,.05],(len(windows),1))
    monkeypatch.setattr(c,'_predict',predict)
    progress=[]
    result=c.classify_audio(path,window_sec=1,batch_size=2,progress=lambda **x:progress.append(x))
    assert len(loads)==1
    assert len(batches)==3
    assert max(map(len,batches))==2
    assert batches[-1][-1]==8000
    assert len(result['raw_spans'])==6
    assert result['raw_spans'][-1]['end_us']==5500000
    assert result['settings']['calibrated'] is False
    assert result['settings']['activation']=='sigmoid'
    assert progress[-1]['completed_windows']==6
    result['spans'][0]['label']='music'
    assert result['raw_spans'][0]['label']=='speech'


def test_real_silent_wav_never_loads_model(tmp_path,monkeypatch):
    monkeypatch.setattr(c,'_load_model',lambda *a: pytest.fail('靜音不可啟動模型'))
    result=c.classify_audio(wav(tmp_path,1,silent=True))
    assert result['raw_spans'][0]['label']=='uncertain'
    assert result['raw_spans'][0]['reason']=='digital_silence'
    assert result['settings']['model_loaded'] is False


def test_predict_sigmoid_and_one_forward_for_batch():
    import torch
    calls=[]
    def extractor(windows,**kwargs):
        calls.append(len(windows))
        return {'input_values':torch.zeros((len(windows),2))}
    def model(**kwargs):
        return SimpleNamespace(logits=torch.tensor([[2.,2.],[0.,0.]]))
    result=c._predict(extractor,model,[np.ones(16),np.ones(16)],16000,'cpu')
    assert calls==[2]
    assert result[0,0]>.8 and result[0,1]>.8
    assert np.allclose(result[1],[.5,.5])


@pytest.mark.parametrize('kwargs',[{'batch_size':0},{'batch_size':65},{'batch_size':True},
    {'window_sec':0},{'window_sec':float('nan')},{'window_sec':float('inf')},{'window_sec':60},
    {'device':'auto'},{'device':'cuda:42'}])
def test_invalid_options_fail_before_decode(monkeypatch,kwargs):
    monkeypatch.setattr(c,'_decode_audio',lambda *a: pytest.fail('應先驗證參數'))
    with pytest.raises(ValueError): c.classify_audio('irrelevant.wav',**kwargs)


def test_empty_audio_is_explicit(tmp_path):
    with pytest.raises(ValueError,match='EMPTY_AUDIO'):c.classify_audio(wav(tmp_path,0))


def test_nonfinite_float_wav_is_rejected_before_model(tmp_path,monkeypatch):
    import soundfile as sf
    path=tmp_path/'nan.wav'
    sf.write(path,np.full(16000,np.nan,dtype=np.float32),16000,subtype='FLOAT')
    monkeypatch.setattr(c,'_load_model',lambda *a:pytest.fail('非有限音訊不可載入模型'))
    with pytest.raises(ValueError,match='NONFINITE_AUDIO'):c.classify_audio(path)


def test_stereo_resampling_and_decode_memory_limit(tmp_path,monkeypatch):
    import soundfile as sf
    path=tmp_path/'stereo.wav'
    sf.write(path,np.ones((48000,2),dtype=np.float32)*.2,48000)
    audio,sr=c._decode_audio(path)
    assert sr==16000 and audio.shape==(16000,)
    assert np.isfinite(audio).all()
    monkeypatch.setattr(c,'MAX_INTERLEAVED_SAMPLES',100)
    with pytest.raises(ValueError,match='AUDIO_TOO_LARGE'):c._decode_audio(path)


def test_empty_override_and_manual_uncertain_remain_reviewable():
    assert c.apply_overrides([],[])==[]
    result=c.apply_overrides([dict(id='a',start_us=0,end_us=100,label='music',sample_start=0,sample_end=16)],
                            [dict(id='o',start_us=20,end_us=50,label='uncertain')])
    assert result[1]['requires_review'] is True
    assert result[1]['raw_sample_start']==0 and 'sample_start' not in result[1]


def test_overrides_split_preserve_original_and_last_wins():
    raw=[dict(id='a',start_us=0,end_us=100,label='music',scores={'music':.95})]
    overrides=[dict(id='first',start_us=20,end_us=80,label='speech',reason='有說話'),
        dict(id='later',start_us=40,end_us=60,label='mixed',reason='保留背景音樂')]
    original=deepcopy(raw);original_overrides=deepcopy(overrides)
    result=c.apply_overrides(raw,overrides)
    assert [(s['start_us'],s['end_us'],s['label']) for s in result]==[
        (0,20,'music'),(20,40,'speech'),(40,60,'mixed'),(60,80,'speech'),(80,100,'music')]
    assert result[2]['model_label']=='music' and result[2]['override_id']=='later'
    result[0]['scores']['music']=0
    assert raw==original and overrides==original_overrides


@pytest.mark.parametrize('override',[
    dict(id='x',start_us=-1,end_us=20,label='speech'),
    dict(id='x',start_us=20,end_us=20,label='speech'),
    dict(id='x',start_us=True,end_us=20,label='speech'),
    dict(id='x',start_us=0,end_us=101,label='speech'),
    dict(id='x',start_us=0,end_us=20,label='silence'),
])
def test_invalid_override(override):
    with pytest.raises(ValueError):c.apply_overrides([dict(id='a',start_us=0,end_us=100,label='music')],[override])


def test_override_cannot_cover_unclassified_gap():
    raw=[dict(id='a',start_us=0,end_us=10,label='speech'),dict(id='b',start_us=20,end_us=30,label='music')]
    with pytest.raises(ValueError,match='UNCOVERED'):c.apply_overrides(raw,[dict(id='o',start_us=5,end_us=25,label='mixed')])


def test_reject_overlapping_raw_and_duplicate_override_ids():
    with pytest.raises(ValueError):c.apply_overrides([dict(id='a',start_us=0,end_us=20,label='speech'),dict(id='b',start_us=10,end_us=30,label='music')],[])
    with pytest.raises(ValueError):c.apply_overrides([dict(id='a',start_us=0,end_us=20,label='speech')],[dict(id='x',start_us=0,end_us=10,label='music')]*2)
