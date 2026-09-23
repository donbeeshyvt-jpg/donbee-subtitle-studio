"""明確指定的模型不可靜默降級；排程符合模型資源設定。"""
import json
import pytest
from fastapi.testclient import TestClient
from app.studio.api import create_app
from app.studio.store import Store, StudioError
from app.studio.worker import Worker


def test_plan_rejects_unknown_provider(tmp_path):
    with TestClient(create_app(tmp_path,start_workers=False)) as c:
        c.get('/v1/session')
        p=c.post('/v1/projects',json={'name':'test'}).json()['project_id']
        source=c.post(f'/v1/projects/{p}/sources',json={'kind':'youtube','url':'https://youtu.be/Yn2mE6_tMC8'}).json()['source_id']
        r=c.post(f'/v1/projects/{p}/plans',json={'source_id':source,'analysis':{'summary':True,'provider_id':'removed'}})
        assert r.status_code==422
        assert r.json()['error']['code']=='PROVIDER_NOT_FOUND'


def test_worker_does_not_replace_missing_model_with_extraction(tmp_path):
    store=Store(tmp_path)
    (tmp_path/'config.json').write_text('{"providers":[]}')
    p=store.create('project',{'name':'test'})['id']
    tr=store.create('transcript',{'cues':[]},p)
    job=store.submit(p,{'kind':'summarize','transcript_revision':tr['id'],'provider_id':'removed'})
    with pytest.raises(StudioError) as error:
        Worker(store,job).provider_work()
    assert error.value.code=='PROVIDER_NOT_FOUND'


@pytest.mark.parametrize('ownership,expected',[('cpu','provider'),('external','gpu')])
def test_child_provider_uses_configured_resource(tmp_path,ownership,expected):
    store=Store(tmp_path)
    (tmp_path/'config.json').write_text(json.dumps({'providers':[{'id':'local','gpu_ownership':ownership}]}))
    p=store.create('project',{'name':'test'})['id']
    root=store.submit(p,{'kind':'workflow'},'workflow')
    child=Worker(store,root).child({'kind':'summarize','provider_id':'local'},'gpu')
    assert store.job(child)['resource']==expected


def test_invalid_model_output_becomes_chinese_job_error_with_excerpt(tmp_path,monkeypatch):
    from app.studio import providers
    store=Store(tmp_path)
    (tmp_path/'config.json').write_text(json.dumps({'providers':[{'id':'local-small','adapter':'openai_compatible','base_url':'http://127.0.0.1:1234/v1',
        'model':'tiny','gpu_ownership':'cpu','timeout_sec':5,'max_retries':1,'max_context_chars':6000,'max_output_tokens':256,'response_format_mode':'json_schema'}]}),encoding='utf-8')
    p=store.create('project',{'name':'test'})['id']
    tr=store.create('transcript',{'cues':[dict(id='a',raw_text='第一個重點',start_us=0,end_us=1000000)]},p)
    job=store.submit(p,{'kind':'correct','transcript_revision':tr['id'],'provider_id':'local-small'})
    def broken(cues,config,glossary=None,*,base_revision=None,title=None,**options):
        error=providers.ProviderError('INVALID_MODEL_OUTPUT'); error.raw_excerpt='{"patches": "oops"'; error.chunk_index=0
        raise error
    monkeypatch.setattr(providers,'correct',broken)
    with pytest.raises(StudioError) as error:
        Worker(store,job).provider_work()
    assert error.value.code=='MODEL_OUTPUT_INVALID'
    assert '原稿已保留' in str(error.value) and 'INVALID_MODEL_OUTPUT' not in str(error.value)
    assert error.value.details['raw_excerpt']=='{"patches": "oops"'
    assert error.value.details['provider_id']=='local-small' and error.value.details['model']=='tiny'


def test_worker_passes_glossary_and_source_title_to_correction(tmp_path,monkeypatch):
    from app.studio import providers
    from app.studio.contracts import JobRequest
    from pydantic import ValidationError
    store=Store(tmp_path)
    (tmp_path/'config.json').write_text(json.dumps({'providers':[{'id':'local','adapter':'openai_compatible','base_url':'http://127.0.0.1:1234/v1',
        'model':'m','gpu_ownership':'cpu','timeout_sec':5,'max_retries':1,'max_context_chars':6000,'max_output_tokens':256,'response_format_mode':'json_schema'}]}),encoding='utf-8')
    p=store.create('project',{'name':'test'})['id']
    source=store.create('source',{'kind':'youtube','title':'彈幕射擊遊戲實況'},p)['id']
    tr=store.create('transcript',{'source_id':source,'cues':[dict(id='a',raw_text='彈步',start_us=0,end_us=1000000)]},p)
    job=store.submit(p,{'kind':'correct','transcript_revision':tr['id'],'provider_id':'local','glossary':['彈幕']})
    seen={}
    def fake(cues,config,glossary=None,*,base_revision=None,title=None,**options):
        seen.update(glossary=glossary,title=title,mode=options.get('mode'))
        return dict(method='llm',patches=[],rejected_patches=[],usage={},chunks=1)
    monkeypatch.setattr(providers,'correct',fake)
    Worker(store,job).provider_work()
    assert seen=={'glossary':['彈幕'],'title':'彈幕射擊遊戲實況','mode':'conservative'}  # 預設保守；積極模式由介面選
    assert JobRequest(kind='correct',transcript_revision='t',glossary=['彈幕','星街']).glossary==['彈幕','星街']
    with pytest.raises(ValidationError):
        JobRequest(kind='correct',transcript_revision='t',glossary=['x']*101)
    with pytest.raises(ValidationError):
        JobRequest(kind='correct',transcript_revision='t',glossary=['字'*81])


@pytest.mark.parametrize('code,fragment',[('PROVIDER_RATE_LIMITED','限流'),('PROVIDER_SERVER_ERROR','伺服器'),('PROVIDER_TIMEOUT','逾時'),
                                          ('PROVIDER_CONNECTION_FAILED','無法連線'),('PROVIDER_AUTH_FAILED','認證'),('PROVIDER_SECRET_MISSING','金鑰')])
@pytest.mark.parametrize('kind',['correct','summarize'])
def test_provider_transport_failures_become_chinese_job_errors_and_keep_transcript(tmp_path,monkeypatch,code,fragment,kind):
    from app.studio import providers
    store=Store(tmp_path)
    (tmp_path/'config.json').write_text(json.dumps({'providers':[{'id':'local','adapter':'openai_compatible','base_url':'http://127.0.0.1:1234/v1',
        'model':'tiny','gpu_ownership':'cpu'}]}),encoding='utf-8')
    p=store.create('project',{'name':'test'})['id']
    tr=store.create('transcript',{'cues':[dict(id='a',raw_text='第一個重點',start_us=0,end_us=1000000)]},p)
    job=store.submit(p,{'kind':kind,'transcript_revision':tr['id'],'provider_id':'local'})
    def failing(*args,**kwargs):
        raise providers.ProviderError(code)
    monkeypatch.setattr(providers,kind,failing)
    with pytest.raises(StudioError) as error:
        Worker(store,job).provider_work()
    assert error.value.code==code
    assert fragment in str(error.value) and '原稿已保留' in str(error.value) and code not in str(error.value)
    assert error.value.details['provider_error']==code and error.value.details['provider_id']=='local'
    assert store.get(tr['id'],'transcript')['cues'][0]['raw_text']=='第一個重點'


def test_http_400_becomes_chinese_hint_about_response_format_mode(tmp_path,monkeypatch):
    from app.studio import providers
    store=Store(tmp_path)
    (tmp_path/'config.json').write_text(json.dumps({'providers':[{'id':'local','adapter':'openai_compatible','base_url':'http://127.0.0.1:1234/v1',
        'model':'tiny','gpu_ownership':'cpu','response_format_mode':'json_object'}]}),encoding='utf-8')
    p=store.create('project',{'name':'test'})['id']
    tr=store.create('transcript',{'cues':[dict(id='a',raw_text='第一個重點',start_us=0,end_us=1000000)]},p)
    job=store.submit(p,{'kind':'correct','transcript_revision':tr['id'],'provider_id':'local'})
    def failing(*args,**kwargs):
        raise providers.ProviderError('PROVIDER_HTTP_400')
    monkeypatch.setattr(providers,'correct',failing)
    with pytest.raises(StudioError) as error:
        Worker(store,job).provider_work()
    assert error.value.code=='PROVIDER_REJECTED_REQUEST'
    assert '回覆格式' in str(error.value) and '原稿已保留' in str(error.value)
    assert error.value.details['provider_error']=='PROVIDER_HTTP_400' and error.value.details['response_format_mode']=='json_object'


@pytest.mark.parametrize('kind',['correct','summarize'])
def test_background_jobs_use_the_key_saved_in_settings_not_only_env(tmp_path,monkeypatch,kind):
    """2026-09-21 真呼叫發現：測試連線找得到介面存的金鑰，但背景的校字／摘要工作回 PROVIDER_SECRET_MISSING
    （worker 只看環境變數，沒讀 data/secrets.json）→ 本機與 API 必須走同一套金鑰解析。"""
    from app.studio import providers, provider_secrets
    monkeypatch.delenv('OPENROUTER_TEST_KEY',raising=False)
    store=Store(tmp_path)
    (tmp_path/'config.json').write_text(json.dumps({'providers':[{'id':'remote','adapter':'openai_compatible','base_url':'http://127.0.0.1:9/v1',
        'model':'m','gpu_ownership':'cpu','timeout_sec':5,'max_retries':1,'max_context_chars':6000,'max_output_tokens':256,
        'response_format_mode':'json_schema','api_key_env':'OPENROUTER_TEST_KEY'}]}),encoding='utf-8')
    provider_secrets.set_secret(tmp_path,'remote','sk-or-v1-'+'a'*64)
    p=store.create('project',{'name':'test'})['id']
    tr=store.create('transcript',{'cues':[dict(id='a',raw_text='彈步',start_us=0,end_us=1000000)]},p)
    job=store.submit(p,{'kind':kind,'transcript_revision':tr['id'],'provider_id':'remote'})
    seen={}
    def fake_request(settings,method,path,body=None):
        seen['authorization']=settings['headers'].get('Authorization')
        raise providers.ProviderError('PROVIDER_SERVER_ERROR')  # 看到請求就好，不必真的回
    monkeypatch.setattr(providers,'_request',fake_request)
    with pytest.raises(StudioError):
        Worker(store,job).provider_work()
    assert seen.get('authorization')=='Bearer sk-or-v1-'+'a'*64  # 用的是介面存的金鑰


def test_worker_passes_the_subtitle_punctuation_setting_to_correction(tmp_path,monkeypatch):
    """2026-09-21：提示詞要知道字幕格式 → 校字工作把「保留標點」設定交給 providers.correct（網頁與 CLI 都送 keep_punctuation）。"""
    from app.studio import providers
    store=Store(tmp_path)
    (tmp_path/'config.json').write_text(json.dumps({'providers':[{'id':'local','adapter':'openai_compatible','base_url':'http://127.0.0.1:1234/v1',
        'model':'m','gpu_ownership':'cpu','timeout_sec':5,'max_retries':1,'max_context_chars':6000,'max_output_tokens':256,'response_format_mode':'json_schema'}]}),encoding='utf-8')
    p=store.create('project',{'name':'test'})['id']
    tr=store.create('transcript',{'cues':[dict(id='a',raw_text='彈步',start_us=0,end_us=1000000)]},p)
    seen=[]
    def fake(cues,config,glossary=None,**options):
        seen.append(options.get('keep_punctuation'))
        return dict(method='llm',patches=[],rejected_patches=[],usage={},chunks=1)
    monkeypatch.setattr(providers,'correct',fake)
    for keep in (False,True):
        job=store.submit(p,{'kind':'correct','transcript_revision':tr['id'],'provider_id':'local','keep_punctuation':keep})
        Worker(store,job).provider_work()
    assert seen==[False,True]
