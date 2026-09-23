"""worker 呼叫 refine 時必須帶入工作 timeout 與 fallback 設定；契約需接受 fallback_engine（M0-4）。"""
import hashlib
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.studio.contracts import JobRequest
from app.studio.store import Store, StudioError
from app.studio.worker import Worker


def test_job_request_accepts_only_whisperx_fallback():
    assert JobRequest(kind='refine',transcript_revision='t',engine='vibevoice',fallback_engine='whisperx').fallback_engine=='whisperx'
    assert JobRequest(kind='refine',transcript_revision='t').fallback_engine is None
    with pytest.raises(ValidationError):
        JobRequest(kind='refine',transcript_revision='t',fallback_engine='vibevoice')


def prepare(tmp_path,body):
    path=tmp_path/'audio.wav'; path.write_bytes(b'original')
    store=Store(tmp_path/'state')
    project=store.create('project',{'name':'精修傳遞'})['id']
    source=store.create('source',{'kind':'local'},project)['id']
    asset=store.create('asset',{'source_id':source,'path':str(path),'map_revision':'m','content_hash':hashlib.sha256(b'original').hexdigest(),
        'source_map':[{'source_start_us':0,'source_end_us':1000000}]},project)
    cues=[{'id':'cue','start_us':0,'end_us':900000,'text':'測試','words':[]}]
    transcript=store.revise(f'transcript:{source}:track','transcript',{'source_id':source,'audio_track_id':'track','asset_ids':[asset['id']],'cues':cues},None,project,initial=None)
    job=store.submit(project,{'kind':'refine','transcript_revision':transcript['id'],**body})
    return store,job,cues


def completed(cues):
    return {'cues':cues,'routing':{'selected':[{'status':'completed','cue_ids':['cue']}]},'warnings':[]}


def test_worker_passes_timeout_and_fallback_to_refine(tmp_path,monkeypatch):
    store,job,cues=prepare(tmp_path,{'engine':'vibevoice','timeout_sec':120,'fallback_engine':'whisperx','cue_ids':['cue']})
    refine=Mock(return_value=completed(cues))
    monkeypatch.setattr('app.studio.asr.refine',refine)
    Worker(store,job).refine_align()
    args,kwargs=refine.call_args
    assert args[2]=='vibevoice'
    assert kwargs['timeout_sec']==120
    assert kwargs['fallback_engine']=='whisperx'


def test_worker_defaults_keep_whisperx_without_fallback(tmp_path,monkeypatch):
    store,job,cues=prepare(tmp_path,{})
    refine=Mock(return_value=completed(cues))
    monkeypatch.setattr('app.studio.asr.refine',refine)
    Worker(store,job).refine_align()
    args,kwargs=refine.call_args
    assert args[2]=='whisperx'
    assert kwargs['timeout_sec']==1800
    assert kwargs['fallback_engine'] is None


def test_worker_reports_batch_failure_cause(tmp_path,monkeypatch):
    store,job,cues=prepare(tmp_path,{'engine':'vibevoice','cue_ids':['cue']})
    routing={'selected':[{'status':'failed','cue_ids':['cue'],'error_type':'StudioError',
        'error':{'code':'VIBEVOICE_CUDA_BUDGET','message':'GPU 記憶體不足'}}]}
    monkeypatch.setattr('app.studio.asr.refine',Mock(return_value={'cues':cues,'routing':routing,'warnings':['refinement_failed_original_preserved']}))
    with pytest.raises(StudioError) as info:
        Worker(store,job).refine_align()
    assert info.value.code=='REFINEMENT_FAILED'
    assert 'GPU 記憶體不足' in str(info.value)


def test_contracts_accept_engine_choice_in_follow_up_and_analysis():
    from app.studio.contracts import Analysis, FollowUp
    assert FollowUp(profile='quality',engine='vibevoice',fallback_engine='whisperx').engine=='vibevoice'
    assert FollowUp().engine=='whisperx' and FollowUp().fallback_engine is None
    assert Analysis(mode='balanced',engine='vibevoice').fallback_engine is None
    with pytest.raises(ValidationError):
        Analysis(engine='hybrid')


def test_follow_up_carries_engine_choice_into_analysis_request():
    from app.studio import worker as worker_module
    follow=worker_module.follow_up_from_analysis({'mode':'quality','engine':'vibevoice','fallback_engine':'whisperx'})
    assert follow=={'kind':'analyze','profile':'quality','engine':'vibevoice','fallback_engine':'whisperx'}
    request=worker_module.analysis_request(follow,{'model':'turbo'},'src',{'id':'asset','audio_track_id':'default'})
    assert request['kind']=='analyze' and request['asset_ids']==['asset'] and request['profile']=='quality'
    assert request['engine']=='vibevoice' and request['fallback_engine']=='whisperx'
    default=worker_module.analysis_request({'kind':'analyze','profile':'balanced'},{},'src',{'id':'a','audio_track_id':'default'})
    assert default['engine']=='whisperx' and default['fallback_engine'] is None
    assert worker_module.follow_up_from_analysis({'mode':'draft'})=={'kind':'analyze','profile':'draft','engine':'whisperx','fallback_engine':None}


def test_worker_passes_language_policy_to_refine(tmp_path,monkeypatch):
    store,job,cues=prepare(tmp_path,{'engine':'whisperx','language_policy':'ja','cue_ids':['cue']})
    refine=Mock(return_value=completed(cues))
    monkeypatch.setattr('app.studio.asr.refine',refine)
    Worker(store,job).refine_align()
    assert refine.call_args.kwargs['language_policy']=='ja'
    (tmp_path/'two').mkdir()
    store2,job2,cues2=prepare(tmp_path/'two',{})
    refine2=Mock(return_value=completed(cues2))
    monkeypatch.setattr('app.studio.asr.refine',refine2)
    Worker(store2,job2).refine_align()
    assert refine2.call_args.kwargs['language_policy']=='auto_ja_zh_en'


def test_quality_refine_passes_only_each_assets_cues_when_transcript_spans_two_assets(tmp_path,monkeypatch):
    """2026-09-18 M2-1 真跑：同一範圍兩個素材（600 秒 vs 610 秒），品質精修 cue_ids＝全部句子 → 第一個素材的 refine 收到它沒涵蓋的句子 → UNKNOWN_CUE_ID。"""
    from app.studio import asr
    short=tmp_path/'short.wav'; short.write_bytes(b'short')
    long=tmp_path/'long.wav'; long.write_bytes(b'long')
    store=Store(tmp_path/'state')
    project=store.create('project',{'name':'兩素材'})['id']
    source=store.create('source',{'kind':'local'},project)['id']
    def asset(path,end):
        return store.create('asset',{'source_id':source,'path':str(path),'map_revision':'m','content_hash':hashlib.sha256(path.read_bytes()).hexdigest(),
            'source_map':[{'source_start_us':0,'source_end_us':end}],'duration_us':end},project)['id']
    a_short,a_long=asset(short,10000000),asset(long,12000000)
    cues=[{'id':'early','start_us':0,'end_us':9000000,'text':'前段','words':[]},{'id':'late','start_us':9500000,'end_us':11500000,'text':'尾段','words':[]}]
    transcript=store.revise(f'transcript:{source}:track','transcript',{'source_id':source,'audio_track_id':'track','asset_ids':[a_short,a_long],'cues':cues},None,project,initial=None)
    job=store.submit(project,{'kind':'refine','transcript_revision':transcript['id'],'cue_ids':['early','late'],'max_refine_audio_ratio':1.0})
    seen=[]
    def fake_refine(selected,path,engine,**kwargs):
        # 真正的 route_refinement 對「不在 selected 裡的 cue_ids」會拋 UNKNOWN_CUE_ID：這裡照樣檢查
        asr.route_refinement(selected,cue_ids=kwargs.get('cue_ids'),max_refine_audio_ratio=1.0,context_us=0)
        seen.append((Path(path).name,sorted(kwargs.get('cue_ids') or [])))
        return {'cues':selected,'routing':{'selected':[{'status':'completed','cue_ids':[c['id'] for c in selected]}]},'warnings':[]}
    monkeypatch.setattr('app.studio.asr.refine',fake_refine)
    result=Worker(store,job).refine_align()
    assert seen==[('short.wav',['early']),('long.wav',['late'])]
    assert {c['id'] for c in store.get(result['transcript_revision'],'transcript')['cues']}=={'early','late'}


def test_refine_uses_the_asset_that_produced_each_cue(tmp_path,monkeypatch):
    """兩個素材時間上都涵蓋同一句時，精修必須用產生該句的素材（cue.asset_id），否則會重聽到錯的音訊並改壞文字（2026-09-18 真實重現）。"""
    short=tmp_path/'short.wav'; short.write_bytes(b'short')
    long=tmp_path/'long.wav'; long.write_bytes(b'long')
    store=Store(tmp_path/'state')
    project=store.create('project',{'name':'素材歸屬'})['id']
    source=store.create('source',{'kind':'local'},project)['id']
    def asset(path,end):
        return store.create('asset',{'source_id':source,'path':str(path),'map_revision':'m','content_hash':hashlib.sha256(path.read_bytes()).hexdigest(),
            'source_map':[{'source_start_us':0,'source_end_us':end}],'duration_us':end},project)['id']
    a_short,a_long=asset(short,10000000),asset(long,12000000)
    cues=[{'id':'one','start_us':1000000,'end_us':3000000,'text':'一','words':[],'asset_id':a_long},
          {'id':'two','start_us':4000000,'end_us':6000000,'text':'二','words':[],'asset_id':a_long}]
    transcript=store.revise(f'transcript:{source}:track','transcript',{'source_id':source,'audio_track_id':'track','asset_ids':[a_short,a_long],'cues':cues},None,project,initial=None)
    job=store.submit(project,{'kind':'refine','transcript_revision':transcript['id'],'cue_ids':['one','two'],'max_refine_audio_ratio':1.0})
    seen=[]
    def fake_refine(selected,path,engine,**kwargs):
        seen.append((Path(path).name,sorted(c['id'] for c in selected)))
        return {'cues':selected,'routing':{'selected':[{'status':'completed','cue_ids':[c['id'] for c in selected]}]},'warnings':[]}
    monkeypatch.setattr('app.studio.asr.refine',fake_refine)
    Worker(store,job).refine_align()
    assert seen==[('long.wav',['one','two'])]


def test_acquire_defaults_to_accurate_boundaries():
    """取得工作預設 accurate：起點精準，與流程契約一致；source_seek 仍可明確指定。"""
    assert JobRequest(kind='acquire',source_id='s').boundary_policy=='accurate'
    assert JobRequest(kind='acquire',source_id='s',boundary_policy='source_seek').boundary_policy=='source_seek'


def test_analyze_request_accepts_auto_align_flag():
    assert JobRequest(kind='analyze',source_id='s').auto_align is True
    assert JobRequest(kind='analyze',source_id='s',auto_align=False).auto_align is False


def test_refining_an_outdated_version_stops_before_doing_any_work(tmp_path,monkeypatch):
    """2026-09-21 真跑：同一份草稿先用 A 模型精修（逐字稿往前走了一版），再用 B 模型精修同一份舊草稿 ——
    B 把整段都轉完（遠端模型等於花了錢）才在存檔時回 REVISION_CONFLICT。要在開始前就檢查、立刻停下，一個模型都不呼叫。"""
    from app.studio import asr
    store,job,_=prepare(tmp_path,{'model':'large-v3','cue_ids':['cue'],'max_refine_audio_ratio':1.0})
    transcript=store.get(store.job(job['id'])['body']['transcript_revision'],'transcript')
    newer=store.revise(f"transcript:{transcript['source_id']}:track",'transcript',{k:v for k,v in transcript.items() if k not in ('id','revision','parent_id')},
                       transcript['id'],store.job(job['id'])['project_id'],initial=None)  # 另一個精修先完成
    called=Mock()
    monkeypatch.setattr(asr,'refine',called)
    with pytest.raises(StudioError) as stale:
        Worker(store,store.job(job['id'])).refine_align()
    assert stale.value.code=='REVISION_CONFLICT' and stale.value.details=={'current_revision':newer['id']}
    called.assert_not_called()  # 沒有任何模型被呼叫


def test_refined_cues_remember_the_asset_that_produced_them(tmp_path,monkeypatch):
    """2026-09-21 真跑：精修後的新句子沒有 asset_id（307 句只有 11 句有）→ 之後再精修／對齊只能靠時間猜素材，
    同一專案有時間重疊的兩份下載時會用錯檔。精修產生的句子要記下產生它的素材。"""
    store,job,cues=prepare(tmp_path,{'cue_ids':['cue']})
    refined=[{'id':'new_cue','start_us':0,'end_us':900000,'text':'精修後','words':[]}]
    monkeypatch.setattr('app.studio.asr.refine',Mock(return_value=completed(refined)))
    result=Worker(store,job).refine_align()
    saved=store.get(result['transcript_revision'],'transcript')
    asset_id=saved['asset_ids'][0]
    assert [c.get('asset_id') for c in saved['cues']]==[asset_id]
