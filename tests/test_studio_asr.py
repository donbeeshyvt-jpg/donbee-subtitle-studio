"""模型替身契約測試；不代表真實 GPU／語意品質通過。"""
import copy
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from app.studio import asr


def cue(identity,start,end,text='文字',lang='zh',**extra):
    return dict(id=identity,start_us=start,end_us=end,raw_text=text,accepted_text=text,lang=lang,alignment_status='segment',words=[],**extra)

def test_vibevoice_refinement_batches_and_restores_source_times(monkeypatch):
    monkeypatch.setattr(asr,'_load_audio',lambda path:np.zeros(20*16000))
    source=[cue(str(i),i*2000000,i*2000000+1000000) for i in range(9)]
    calls=[]
    def batch(pieces,timeout):
        calls.append(len(pieces))
        return [{'cues':[cue('new'+str(i),0,1000000)]} for i in range(len(pieces))]
    monkeypatch.setattr(asr,'_vibevoice_batch',batch)
    result=asr.refine(source,'audio.wav',engine='vibevoice',cue_ids=[c['id'] for c in source],context_us=0,max_refine_audio_ratio=1)
    assert calls==[8,1]
    assert [c['start_us'] for c in result['cues']]==[c['start_us'] for c in source]
    assert all(row['status']=='completed' for row in result['routing']['selected'])


def test_route_respects_budget_padding_and_reason():
    cues=[cue('a',0,1000000,review_flags=['low_log_probability']),cue('b',1000000,2000000,review_flags=['repetition']),cue('c',2000000,10000000)]
    result=asr.route_refinement(cues,max_refine_audio_ratio=.2,context_us=100000,source_start_us=0,source_end_us=10000000)
    assert result['processed_audio_us']<=2000000
    assert len(result['selected'])==1
    assert result['selected'][0]['reasons']==['low_log_probability']
    assert any(item['reason']=='budget_exhausted' for item in result['deferred'])


def test_route_manual_selection_prioritized_and_unknown_id_rejected():
    cues=[cue('a',0,1000000,review_flags=['repetition']),cue('b',1000000,2000000)]
    result=asr.route_refinement(cues,cue_ids=['b'],max_refine_audio_ratio=.5,context_us=0)
    assert result['selected'][0]['cue_ids']==['b']
    assert result['selected'][0]['reasons']==['user_selected']
    with pytest.raises(ValueError):
        asr.route_refinement(cues,cue_ids=['missing'])


def test_align_uses_each_language_and_source_offset(monkeypatch):
    called=[]
    def load(language_code,device):
        called.append(language_code)
        return language_code,{}
    def align(segments,model,meta,audio,device,return_char_alignments):
        assert len(segments)==1
        return {'segments':[dict(text=segments[0]['text'],words=[dict(word=segments[0]['text'],start=segments[0]['start'],end=segments[0]['end']),dict(word='?',start=None,end=None)])]}
    monkeypatch.setitem(sys.modules,'whisperx',SimpleNamespace(load_audio=lambda path:np.zeros(16000*10),load_align_model=load,align=align))
    source=[cue('a',6600000000,6601000000,'你好','zh'),cue('b',6602000000,6603000000,'hello','en')]
    before=copy.deepcopy(source)
    result=asr.align(source,'audio.wav',6600000000,device='cpu')
    assert called==['zh','en']
    assert result['cues'][0]['words'][0]['start_us']==6600000000
    assert result['cues'][0]['words'][1]['start_us'] is None
    assert source==before


def test_align_only_requested_cue_and_failure_is_explicit(monkeypatch):
    def fail(**kwargs): raise RuntimeError('no model')
    monkeypatch.setitem(sys.modules,'whisperx',SimpleNamespace(load_audio=lambda path:np.zeros(16000*4),load_align_model=fail))
    source=[cue('a',0,1000000),cue('b',2000000,3000000)]
    result=asr.align(source,'audio.wav',device='cpu',cue_ids=['a'])
    assert result['cues'][1]==source[1]
    assert result['warnings'] and result['cues'][0]['alignment_status']=='unaligned'


def test_refine_only_selected_span_and_preserves_other_cues(monkeypatch):
    source=[cue('a',0,1000000,review_flags=['repetition']),cue('b',2000000,10000000)]
    calls=[]
    monkeypatch.setattr(asr,'_load_audio',lambda path:np.zeros(160000))
    def transcribe(audio,**kwargs):
        calls.append(len(audio))
        return {'cues':[cue('new',0,1000000,'修正')], 'settings':{'engine':'fake_contract'}}
    monkeypatch.setattr(asr,'_transcribe_audio',transcribe)
    before=copy.deepcopy(source)
    result=asr.refine(source,'audio.wav',engine='whisperx',device='cpu',context_us=0,max_refine_audio_ratio=.2)
    assert calls==[16000]
    assert result['cues'][0]['raw_text']=='修正'
    assert result['cues'][1]==source[1]
    assert source==before


def test_refine_failure_preserves_original_and_records_error(monkeypatch):
    monkeypatch.setattr(asr,'_load_audio',lambda path:np.zeros(160000))
    def fail(audio,**kwargs): raise RuntimeError('failed')
    monkeypatch.setattr(asr,'_transcribe_audio',fail)
    source=[cue('a',0,1000000,review_flags=['repetition']),cue('b',2000000,10000000)]
    result=asr.refine(source,'audio.wav',device='cpu',context_us=0,max_refine_audio_ratio=.2)
    assert result['cues']==source
    assert result['routing']['selected'][0]['status']=='failed'
    assert result['warnings']


def test_draft_preserves_raw_words_and_per_segment_languages(monkeypatch):
    segments=[SimpleNamespace(text='\u7b80\u4f53',start=0,end=1,words=[SimpleNamespace(word='\u7b80\u4f53',start=0,end=1,probability=.8)],avg_logprob=-.3,compression_ratio=1),
              SimpleNamespace(text='Hello',start=2,end=3,words=[SimpleNamespace(word='Hello',start=None,end=None,probability=None)],avg_logprob=-2,compression_ratio=3)]
    engine=SimpleNamespace(transcribe=lambda *args,**kwargs:(iter(segments),SimpleNamespace(language='zh',duration=4)))
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=lambda *args,**kwargs:engine))
    monkeypatch.setattr(asr,'_detect_language',lambda text,default=None:'en' if text=='Hello' else 'zh')
    result=asr.draft('audio.wav',device='cpu')
    assert [item['lang'] for item in result['cues']]==['zh','en']
    assert result['cues'][0]['words'][0]['text']=='\u7b80\u4f53'
    assert result['cues'][0]['normalized_text']=='簡體'
    assert result['cues'][1]['words'][0]['start_us'] is None
    assert 'repetition' in result['cues'][1]['review_flags']


def test_manual_edits_are_proposals_and_failed_refinement_never_overwrites(monkeypatch):
    monkeypatch.setattr(asr,'_load_audio',lambda path:np.zeros(160000))
    monkeypatch.setattr(asr,'_transcribe_audio',lambda audio,**kwargs:{'cues':[cue('n',0,1000000,'模型結果')]})
    source=[cue('a',0,1000000,'人工確認',edited=True),cue('b',2000000,10000000)]
    result=asr.refine(source,'audio.wav',cue_ids=['a'],max_refine_audio_ratio=.2,context_us=0,device='cpu')
    assert result['cues']==source
    assert result['routing']['selected'][0]['status']=='proposed'
    assert result['routing']['selected'][0]['replacement_cues'][0]['raw_text']=='模型結果'


def test_align_rejects_audio_out_of_range(monkeypatch):
    monkeypatch.setitem(sys.modules,'whisperx',SimpleNamespace(load_audio=lambda path:np.zeros(16000)))
    with pytest.raises(ValueError,match='ALIGNMENT_AUDIO_RANGE_UNAVAILABLE'):
        asr.align([cue('a',0,2000000)],'audio.wav',device='cpu')


def test_mixed_language_alignment_word_order_is_original_text_order(monkeypatch):
    def load(language_code,device): return language_code,{}
    def alignment(segments,model,meta,audio,device,return_char_alignments):
        text=segments[0]['text'].strip()
        position={'今天':0,'WhisperX':1,'測試':2}[text]
        return {'segments':[{'words':[{'word':text,'start':position,'end':position+.5}]}]}
    monkeypatch.setitem(sys.modules,'whisperx',SimpleNamespace(load_audio=lambda path:np.zeros(64000),load_align_model=load,align=alignment))
    result=asr.align([cue('a',0,4000000,'今天WhisperX測試')],'audio.wav',device='cpu')
    assert [word['text'] for word in result['cues'][0]['words']]==['今天','WhisperX','測試']


def test_refinement_rejects_negative_source_offset(monkeypatch):
    monkeypatch.setattr(asr,'_load_audio',lambda path:np.zeros(16000))
    with pytest.raises(ValueError,match='INVALID_SOURCE_OFFSET'):
        asr.refine([cue('a',0,1000000)],'audio.wav',source_offset_us=-1)

def test_draft_retries_disallowed_detected_language_once(monkeypatch):
    calls=[]
    segment=SimpleNamespace(text='Hello',start=0,end=1,words=[],avg_logprob=-.3,compression_ratio=1)
    def transcribe(*args,**kwargs):
        calls.append(kwargs)
        if len(calls)==1:return iter([]),SimpleNamespace(language='ko',duration=60,all_language_probs=[('ko',.7),('ja',.1),('zh',.18),('en',.02)])
        return iter([segment]),SimpleNamespace(language='zh',duration=60)
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=lambda *args,**kwargs:SimpleNamespace(transcribe=transcribe)))
    monkeypatch.setattr(asr,'_detect_language',lambda text,default=None:default)
    result=asr.draft('audio.wav',device='cpu',language_policy='auto_ja_zh_en')
    assert len(calls)==2 and calls[1]['language']=='zh'
    assert result['settings']['detected_language']=='ko'
    assert result['settings']['language_used']=='zh'
    assert result['settings']['language_fallback_reason']=='outside_policy_probability'


def test_draft_context_hint_used_only_for_disallowed_detection(monkeypatch):
    calls=[]
    def transcribe(*args,**kwargs):
        calls.append(kwargs)
        return iter([]),SimpleNamespace(language='ko' if len(calls)==1 else 'ja',duration=1,all_language_probs=[('ko',.7),('zh',.3)])
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=lambda *args,**kwargs:SimpleNamespace(transcribe=transcribe)))
    result=asr.draft('audio.wav',device='cpu',language_hint='ja')
    assert calls[1]['language']=='ja'
    assert result['settings']['language_fallback_reason']=='outside_policy_context_hint'


def test_draft_disallowed_language_without_evidence_fails(monkeypatch):
    calls=[]
    def transcribe(*args,**kwargs):
        calls.append(kwargs)
        return iter([]),SimpleNamespace(language='ko',duration=1,all_language_probs=[('ko',1.0)])
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=lambda *args,**kwargs:SimpleNamespace(transcribe=transcribe)))
    with pytest.raises(ValueError,match='UNSUPPORTED_DETECTED_LANGUAGE'):
        asr.draft('audio.wav',device='cpu')
    assert len(calls)==1


@pytest.mark.parametrize('language',['zh','ja','en'])
def test_draft_fixed_language_never_autodetects_or_retries(monkeypatch,language):
    calls=[]
    def transcribe(*args,**kwargs):
        calls.append(kwargs)
        return iter([]),SimpleNamespace(language=language,duration=1)
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=lambda *args,**kwargs:SimpleNamespace(transcribe=transcribe)))
    result=asr.draft('audio.wav',device='cpu',language_policy=language,language_hint='ja')
    assert len(calls)==1 and calls[0]['language']==language
    assert result['settings']['language_used']==language
    assert result['settings']['language_fallback_reason'] is None


def test_draft_invalid_language_policy_does_not_load_model(monkeypatch):
    def model(*args,**kwargs):raise AssertionError('model must not load')
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=model))
    with pytest.raises(ValueError,match='INVALID_LANGUAGE_POLICY'):
        asr.draft('audio.wav',language_policy='fr')

def test_draft_allowed_detection_is_not_overridden_by_hint(monkeypatch):
    calls=[]
    def transcribe(*args,**kwargs):
        calls.append(kwargs)
        return iter([]),SimpleNamespace(language='en',duration=1,all_language_probs=[('en',.8),('zh',.2)])
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=lambda *args,**kwargs:SimpleNamespace(transcribe=transcribe)))
    result=asr.draft('audio.wav',device='cpu',language_hint='zh')
    assert len(calls)==1 and 'language' not in calls[0]
    assert result['settings']['language_used']=='en'
    assert result['settings']['language_fallback_reason'] is None


def test_draft_does_not_loop_when_forced_retry_reports_wrong_language(monkeypatch):
    calls=[]
    def transcribe(*args,**kwargs):
        calls.append(kwargs)
        return iter([]),SimpleNamespace(language='ko',duration=1,all_language_probs=[('zh',.1)])
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=lambda *args,**kwargs:SimpleNamespace(transcribe=transcribe)))
    with pytest.raises(ValueError,match='forced language mismatch'):
        asr.draft('audio.wav',device='cpu')
    assert len(calls)==2

def test_vad_guard_recovers_energetic_discarded_audio(monkeypatch):
    calls=[]
    def transcribe(*args,**kwargs):
        calls.append(kwargs)
        kept=9 if kwargs['vad_filter'] else 60
        segment=SimpleNamespace(text='後半語音',start=40,end=45,words=[],avg_logprob=-.5,compression_ratio=1)
        return iter([segment] if not kwargs['vad_filter'] else []),SimpleNamespace(language='zh',duration=60,duration_after_vad=kept)
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=lambda *args,**kwargs:SimpleNamespace(transcribe=transcribe)))
    result=asr.draft(np.ones(60*16000,dtype=np.float32)*.1,device='cpu',language_policy='zh')
    assert calls[0]['vad_filter'] is True and calls[1]['vad_filter'] is False
    # 2026-09-20 起草稿會補轉 3 秒以上、有聲音的空檔（calls[2:]）；替身回的段落落在空檔外，不會多出句子
    assert len(calls)==4 and result['settings']['gap_fill']=={'gaps_checked':2,'cues_added':0,'min_gap_sec':3.0}
    assert result['settings']['vad_guard']['action']=='preserve_uncertain_audio'
    assert result['cues'][0]['start_us']==40000000


def test_vad_guard_keeps_silence_optimization():
    samples=np.zeros(16000*60,dtype=np.float32);samples[:16000*8]=.1
    decision=asr._vad_safety_decision(samples,60,9)
    assert decision['action']=='keep_vad'
    assert decision['energetic_audio_sec']==8


def test_refine_budget_uses_known_analyzed_audio_not_only_cue_duration():
    result=asr.route_refinement([cue('a',1000000,2000000,review_flags=['uncertain'])],source_start_us=0,source_end_us=60000000,max_refine_audio_ratio=.15,context_us=300000)
    assert result['budget_us']==9000000
    assert result['processed_audio_us']==1600000
    assert result['budget_basis']=='provided_audio_span'


def test_bounded_model_session_reuses_identity_then_releases(monkeypatch):
    loads=[]
    def make_model(model,**kwargs):
        loads.append((model,kwargs['device'],kwargs['compute_type']))
        return SimpleNamespace(transcribe=lambda *a,**k:(iter([]),SimpleNamespace(language='zh',duration=1)))
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=make_model))
    with asr._model_session():
        asr.draft('a.wav',model='turbo',device='cpu')
        asr.draft('b.wav',model='turbo',device='cpu')
        asr.draft('b.wav',model='large-v3',device='cpu')
    asr.draft('c.wav',model='turbo',device='cpu')
    assert [r[0] for r in loads]==['turbo','large-v3','turbo']

def test_refine_reuses_one_engine_across_selected_chunks(monkeypatch):
    loads=[]
    def make_model(model,**kwargs):
        loads.append(model)
        def transcribe(audio,**kwargs):
            row=SimpleNamespace(text='Hello',start=0,end=.5,words=[],avg_logprob=-.3,compression_ratio=1)
            return iter([row]),SimpleNamespace(language='en',duration=len(audio)/16000,duration_after_vad=len(audio)/16000)
        return SimpleNamespace(transcribe=transcribe)
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=make_model))
    monkeypatch.setattr(asr,'_load_audio',lambda path:np.ones(160000,dtype=np.float32)*.1)
    source=[cue('a',0,1000000,review_flags=['uncertain']),cue('b',2000000,3000000,review_flags=['uncertain']),cue('c',4000000,10000000)]
    result=asr.refine(source,'audio.wav',device='cpu',max_refine_audio_ratio=.5,context_us=0)
    assert loads==['large-v3']
    assert len(result['routing']['selected'])==2
    assert all(s['status']=='completed' for s in result['routing']['selected'])
    assert asr._scoped_models.get() is None


def test_vibevoice_batch_failure_marks_whole_batch_failed_and_keeps_original(monkeypatch):
    monkeypatch.setattr(asr,'_load_audio',lambda path:np.zeros(20*16000))
    source=[cue(str(i),i*2000000,i*2000000+1000000) for i in range(9)]
    calls=[]
    def batch(pieces,timeout):
        calls.append(len(pieces)); raise RuntimeError('VIBEVOICE_WORKER_FAILED')
    monkeypatch.setattr(asr,'_vibevoice_batch',batch)
    result=asr.refine(source,'audio.wav',engine='vibevoice',cue_ids=[c['id'] for c in source],context_us=0,max_refine_audio_ratio=1)
    assert calls==[8]  # 第一批失敗後，同一次工作不再逐批重啟已失敗的模型
    rows=result['routing']['selected']
    assert [row['status'] for row in rows]==['failed']*9
    assert all(row['error_type']=='RuntimeError' for row in rows)
    assert [c['id'] for c in result['cues']]==[c['id'] for c in source]
    assert 'refinement_failed_original_preserved' in result['warnings']


def test_vibevoice_batch_failure_falls_back_to_whisperx_per_span(monkeypatch):
    monkeypatch.setattr(asr,'_load_audio',lambda path:np.zeros(20*16000))
    source=[cue(str(i),i*2000000,i*2000000+1000000) for i in range(3)]
    def batch(pieces,timeout): raise RuntimeError('VIBEVOICE_WORKER_FAILED')
    monkeypatch.setattr(asr,'_vibevoice_batch',batch)
    used=[]
    def transcribe(piece,model,device,**extra):
        used.append(len(piece)); return {'cues':[cue('w'+str(len(used)),0,1000000)]}
    monkeypatch.setattr(asr,'_transcribe_audio',transcribe)
    result=asr.refine(source,'audio.wav',engine='vibevoice',fallback_engine='whisperx',cue_ids=[c['id'] for c in source],context_us=0,max_refine_audio_ratio=1)
    assert used==[16000]*3
    rows=result['routing']['selected']
    assert [row['status'] for row in rows]==['completed']*3
    assert all(row['engine']=='whisperx' and row['fallback_reason']=='RuntimeError' for row in rows)
    assert all(c['refinement_provenance']['engine']=='whisperx' for c in result['cues'])


def test_vibevoice_batches_split_by_count_and_total_seconds():
    pieces=[np.zeros(16000*100)]*3+[np.zeros(16000)]*9
    assert asr._vibevoice_batches(pieces)==[[0],[1],[2,3,4,5,6,7,8,9],[10,11]]


def test_refine_keeps_each_span_language_and_passes_policy(monkeypatch):
    """草稿判定為日文的句子，精修不得逐段重新自動偵測而改寫成中文；政策與每段多數語言要傳給轉錄。"""
    monkeypatch.setattr(asr,'_load_audio',lambda path:np.zeros(20*16000))
    source=[cue('a',0,1000000,text='ハロー',lang='ja'),cue('b',2000000,3000000,text='你好',lang='zh'),cue('c',4000000,5000000,text='hello',lang='en')]
    seen=[]
    def transcribe(piece,*,model,device,language_policy='auto_ja_zh_en',language_hint=None,**extra):
        seen.append((language_policy,language_hint))
        return {'cues':[cue('n'+str(len(seen)),0,1000000,lang=language_hint or 'zh')]}
    monkeypatch.setattr(asr,'_transcribe_audio',transcribe)
    result=asr.refine(source,'audio.wav',engine='whisperx',cue_ids=['a','b','c'],context_us=0,max_refine_audio_ratio=1,language_policy='auto_ja_zh_en')
    assert seen==[('auto_ja_zh_en','ja'),('auto_ja_zh_en','zh'),('auto_ja_zh_en','en')]
    assert all(row['status']=='completed' for row in result['routing']['selected'])
    seen.clear()
    asr.refine(source,'audio.wav',engine='whisperx',cue_ids=['a'],context_us=0,max_refine_audio_ratio=1,language_policy='zh')
    assert seen==[('zh','ja')], '固定語言政策照傳，轉錄端以政策為準'
    assert asr._span_language_hint([cue('x',0,1,lang='zh'),cue('y',0,1,lang='ja'),cue('z',0,1,lang='ja')])=='ja'
    assert asr._span_language_hint([cue('x',0,1,lang='unknown')]) is None


def test_draft_auto_policy_detects_language_per_segment_and_uses_more_audio_for_file_language(monkeypatch):
    """開頭是日文歌、後面有中文的音檔：自動政策要逐段偵測語言（multilingual），且檔案語言不能只看前 30 秒。"""
    calls=[]
    def transcribe(*args,**kwargs):
        calls.append(kwargs)
        return iter([]),SimpleNamespace(language='ja',duration=120,all_language_probs=[('ja',.6),('zh',.4)])
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=lambda *args,**kwargs:SimpleNamespace(transcribe=transcribe)))
    asr.draft('audio.wav',device='cpu',language_policy='auto_ja_zh_en')
    assert calls[0]['multilingual'] is True and calls[0]['language_detection_segments']>=3 and 'language' not in calls[0]
    calls.clear()
    asr.draft('audio.wav',device='cpu',language_policy='zh')
    assert calls[0]['language']=='zh' and not calls[0].get('multilingual'), '固定語言時不做逐段偵測'


def test_draft_flags_known_hallucination_phrases_and_routes_them_for_refinement(monkeypatch):
    rows=[SimpleNamespace(text='中文字幕 沛隊字幕小組',start=0,end=2,words=[],avg_logprob=-.2,compression_ratio=1),
          SimpleNamespace(text='ご視聴ありがとうございました',start=3,end=5,words=[],avg_logprob=-.2,compression_ratio=1),
          SimpleNamespace(text='我們繼續玩',start=6,end=8,words=[],avg_logprob=-.2,compression_ratio=1)]
    def transcribe(*args,**kwargs):
        return iter(rows),SimpleNamespace(language='zh',duration=10,duration_after_vad=10)
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=lambda *args,**kwargs:SimpleNamespace(transcribe=transcribe)))
    result=asr.draft('audio.wav',device='cpu',language_policy='zh')
    flags=[c['review_flags'] for c in result['cues']]
    assert 'possible_hallucination' in flags[0] and 'possible_hallucination' in flags[1] and 'possible_hallucination' not in flags[2]
    routing=asr.route_refinement(result['cues'],max_refine_audio_ratio=1,context_us=0)
    assert [item['reasons'] for item in routing['selected']]==[['possible_hallucination'],['possible_hallucination']]


def test_chinese_text_is_fully_traditional_and_uses_full_width_punctuation():
    """2026-09-21 真跑（OpenRouter nvidia 轉錄）：
    1. 「这个是大陆游戏耶」轉一次只到「大陸游戲」（被斷成「陸游」），要轉到不再變為止 →「大陸遊戲」。
    2. 中文裡夾半形標點（「好好好, 我要開入」「我在哪?」）→ 全形，才符合繁體中文字幕格式；英文片段不動。"""
    from app.studio.asr import _normalize
    assert _normalize('这个是大陆游戏耶。', 'zh') == '這個是大陸遊戲耶。'
    assert _normalize('好好好, 我要開入, 我看到。', 'zh') == '好好好，我要開入，我看到。'
    assert _normalize('我在哪?', 'zh') == '我在哪？'
    assert _normalize('彭彭, OK', 'zh') == '彭彭，OK'
    assert _normalize('PICO PARK, OK?', 'zh') == 'PICO PARK, OK?'  # 英文照舊
    assert _normalize('3.5 倍', 'zh') == '3.5 倍'  # 數字的小數點不動
    assert _normalize('あたましい, です', 'ja') == 'あたましい, です'  # 非中文不動
