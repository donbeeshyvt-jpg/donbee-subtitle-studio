import json

import httpx
import pytest

from app.studio.cli import main, parse_time, parse_range


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_user_video_time_range():
    assert parse_time('1:50:00')==6600000000
    assert parse_range('1:50:00-2:00:00')=={'start_us':6600000000,'end_us':7200000000}
    assert parse_time('1.000001')==1000001
    for text in ['-1','NaN','1:70','1:2:70','1:2:3:4']:
        with pytest.raises(ValueError):
            parse_time(text)


def test_download_submission_and_clean_json(capsys,monkeypatch):
    monkeypatch.setenv('STUDIO_API_TOKEN','test-token')
    calls=[]
    def handler(request):
        calls.append(request)
        assert request.headers['Authorization']=='Bearer test-token'
        return httpx.Response(202,json={'id':'job_1','status':'queued'})
    with client(handler) as api:
        result=main(['download','--project','p','--source','s','--audio-only','--range','1:50:00-2:00:00','--analyze','draft','--json'],client=api)
    assert result==0
    assert json.loads(capsys.readouterr().out)['id']=='job_1'
    payload=json.loads(calls[0].content)
    assert payload['kind']=='acquire' and payload['asset_kind']=='audio'
    assert payload['follow_up']=={'kind':'analyze','profile':'draft'}
    assert payload['ranges'][0]['start_us']==6600000000


def test_clean_command_reports_what_it_freed(capsys,monkeypatch):
    """M5-1 F06：CLI 也能清資料目錄暫存（--dry-run 只試算），輸出與 API 同一份 JSON。"""
    monkeypatch.setenv('STUDIO_API_TOKEN','test-token')
    seen=[]
    def handler(request):
        seen.append(request)
        return httpx.Response(200,json={'removed_job_dirs':2,'removed_artifacts':0,'removed_staging':1,
                                        'freed_bytes':4096,'kept_artifacts':7,'kept_job_dirs':1,'dry_run':True})
    with client(handler) as api:
        assert main(['clean','--dry-run','--json'],client=api)==0
    assert json.loads(seen[0].content)=={'dry_run':True}
    assert str(seen[0].url).endswith('/v1/maintenance/cleanup')
    assert json.loads(capsys.readouterr().out)['freed_bytes']==4096


@pytest.mark.parametrize('status,code', [('succeeded',0),('failed',3),('cancelled',4),('interrupted',3)])
def test_wait_exit_codes(status,code,capsys):
    def handler(request):
        return httpx.Response(200,json={'id':'j','status':status})
    with client(handler) as api:
        assert main(['job','wait','j','--json'],client=api)==code
    assert json.loads(capsys.readouterr().out)['status']==status


@pytest.mark.parametrize('status,code',[(422,2),(409,6),(500,3)])
def test_http_error_exit_codes(status,code,capsys):
    with client(lambda request:httpx.Response(status,json={'error':{'code':'TEST','message':'錯誤'}})) as api:
        assert main(['project','list','--json'],client=api)==code
    assert json.loads(capsys.readouterr().out)['error']['code']=='TEST'


def test_wait_timeout_is_not_backend_cancel(capsys):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'id':'j','status':'running'})
    with client(handler) as api:
        assert main(['job','wait','j','--wait-timeout','0.01','--poll-interval','0.01'],client=api)==5
    assert all(request.method=='GET' for request in calls)
    assert json.loads(capsys.readouterr().out)['job_cancelled'] is False


def test_events_jsonl_and_event_cursor(capsys):
    def handler(request):
        assert request.headers['Last-Event-ID']=='4'
        return httpx.Response(200,text=': heartbeat\n\nid: 5\ndata: {"id":5,"type":"job.succeeded"}\n\n',headers={'Content-Type':'text/event-stream'})
    with client(handler) as api:
        assert main(['job','events','j','--after','4','--jsonl'],client=api)==0
    assert json.loads(capsys.readouterr().out)['id']==5


def test_interrupt_only_cancels_when_requested(capsys):
    for cancel in (False,True):
        calls=[]
        def handler(request):
            calls.append(request)
            if request.method=='GET':
                raise KeyboardInterrupt()
            return httpx.Response(202,json={'id':'j','status':'cancelling'})
        with client(handler) as api:
            assert main(['job','wait','j']+(['--cancel-on-interrupt'] if cancel else []),client=api)==4
        payload=json.loads(capsys.readouterr().out)
        assert payload['job_id']=='j'
        assert len(calls)==(2 if cancel else 1)


def test_parser_invalid_arguments_are_json_error(capsys):
    assert main(['download','--project','p'])==2
    assert json.loads(capsys.readouterr().out)['error']['code']=='INVALID_ARGUMENTS'


def test_artifact_download_refuses_overwrite(tmp_path,capsys):
    output=tmp_path/'existing.srt'
    output.write_text('original')
    with client(lambda request:httpx.Response(200,content=b'new')) as api:
        assert main(['artifact','get','a','--out',str(output)],client=api)==2
    assert output.read_text()=='original'


def test_unreachable_service_returns_five(capsys):
    def handler(request):
        raise httpx.ConnectError('private internal info')
    with client(handler) as api:
        assert main(['doctor','--json'],client=api)==5
    assert 'private internal info' not in capsys.readouterr().out


def test_real_api_same_project_and_sequence(tmp_path,monkeypatch,capsys):
    from fastapi.testclient import TestClient
    from app.studio.api import create_app
    app=create_app(tmp_path,start_workers=False)
    monkeypatch.setenv('STUDIO_DATA_DIR',str(tmp_path))
    monkeypatch.delenv('STUDIO_API_TOKEN',raising=False)
    with TestClient(app) as api:
        assert main(['project','create','--name','CLI 共用專案','--json'],client=api)==0
        project=json.loads(capsys.readouterr().out)['id']
        assert main(['source','add','--project',project,'--url','https://www.youtube.com/live/Yn2mE6_tMC8'],client=api)==0
        source=json.loads(capsys.readouterr().out)['id']
        path=tmp_path/'sequence.json'
        path.write_text(json.dumps(dict(base_revision='seq_00',source_id=source,items=[dict(id='one',kind='clip',start_us=6600000000,end_us=6660000000,name='第一段')])),'utf-8')
        assert main(['sequence','set','--project',project,'--file',str(path)],client=api)==0
        saved=json.loads(capsys.readouterr().out)
        assert main(['sequence','get','--project',project],client=api)==0
        assert json.loads(capsys.readouterr().out)['id']==saved['id']
        assert main(['download','--project',project,'--source',source,'--audio-only','--range','1:50:00-1:51:00'],client=api)==0
        job=json.loads(capsys.readouterr().out)
        assert job['status']=='queued'


def test_stream_error_is_json_and_nonzero(capsys,tmp_path):
    with client(lambda request:httpx.Response(404,json={'error':{'code':'NOT_FOUND','message':'missing'}})) as api:
        assert main(['artifact','get','a','--out',str(tmp_path/'file.srt')],client=api)==2
    assert json.loads(capsys.readouterr().out)['error']['code']=='NOT_FOUND'


def test_correct_cli_passes_glossary_and_applies_high_confidence_patches(capsys,monkeypatch):
    """LLM 輸出的 CLI 用法：--glossary 帶詞彙表；--wait --apply 把高信心修正寫進逐字稿並輸出摘要。"""
    monkeypatch.setenv('STUDIO_API_TOKEN','test-token')
    calls=[]
    patches=[dict(cue_id='c1',original_text='彈步遊戲',replacement_text='彈幕遊戲',reason='同音錯字',confidence='high',base_revision='tr_1'),
             dict(cue_id='c2',original_text='我在你',replacement_text='我跟你',reason='推測',confidence='low',base_revision='tr_1')]
    def handler(request):
        calls.append(request)
        if request.method=='POST' and request.url.path.endswith('/jobs'):
            return httpx.Response(202,json={'id':'job_c','status':'queued'})
        if request.method=='GET' and request.url.path.endswith('/jobs/job_c'):
            return httpx.Response(200,json={'id':'job_c','status':'succeeded','result':{'patches':patches,'rejected_patches':[{'cue_id':'c3'}],'ignored_no_change':4}})
        if request.method=='POST' and request.url.path.endswith('/transcripts/tr_1/edits'):
            return httpx.Response(201,json={'id':'tr_2','cues':[]})
        return httpx.Response(404,json={'error':{'code':'NOT_FOUND'}})
    with client(handler) as api:
        result=main(['correct','--project','p','--transcript','tr_1','--provider','local-lmstudio','--glossary','彭彭,PICO PARK','--wait','--apply','--json'],client=api)
    assert result==0
    out=json.loads(capsys.readouterr().out)
    submitted=json.loads(calls[0].content)
    assert submitted['kind']=='correct' and submitted['glossary']==['彭彭','PICO PARK']
    edit=[c for c in calls if c.url.path.endswith('/edits')]
    assert len(edit)==1
    body=json.loads(edit[0].content)
    assert body['base_revision']=='tr_1' and body['edits']==[{'cue_id':'c1','text':'彈幕遊戲'}]  # 只套用高信心
    assert body['origin']=='cli' and body['job_id']=='job_c'  # 修改紀錄標明來源
    assert out['applied']==1 and out['skipped_low_confidence']==1 and out['rejected']==1 and out['transcript_revision']=='tr_2'
    assert out['patches'][0]['cue_id']=='c1'


def test_correct_apply_requires_wait_and_download_defaults_to_accurate(capsys,monkeypatch):
    monkeypatch.setenv('STUDIO_API_TOKEN','test-token')
    assert main(['correct','--project','p','--transcript','tr_1','--apply','--json'])==2
    assert json.loads(capsys.readouterr().out)['error']['code']=='INVALID_ARGUMENTS'
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(202,json={'id':'job_1','status':'queued'})
    with client(handler) as api:
        assert main(['download','--project','p','--source','s','--audio-only','--range','1:50:00-2:00:00','--json'],client=api)==0
    assert json.loads(calls[0].content)['boundary_policy']=='accurate'


def test_export_cli_can_wait_for_word_alignment(capsys,monkeypatch):
    # 2026-09-19：CLI／API 改完字馬上匯出，也要能等（或自動補排）逐詞對齊，與網頁「匯出 SRT」一致
    monkeypatch.setenv('STUDIO_API_TOKEN','test-token')
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(202,json={'id':'job_1','status':'queued'})
    with client(handler) as api:
        assert main(['export','--project','p','--sequence','seq_1','--transcript','tr_1','--formats','srt','--await-alignment','--json'],client=api)==0
        assert main(['export','--project','p','--sequence','seq_1','--transcript','tr_1','--formats','srt','--json'],client=api)==0
        assert main(['export','--project','p','--sequence','seq_1','--transcript','tr_1','--formats','srt','--sentence-timing','--json'],client=api)==0
    assert json.loads(calls[0].content)['await_alignment'] is True
    # 2026-09-21：預設也等對齊（與網頁、預覽一致）；要句子時間請明講 --sentence-timing
    assert json.loads(calls[1].content)['await_alignment'] is True
    assert json.loads(calls[2].content)['await_alignment'] is False


def test_cli_reads_glossary_reference_and_asr_hints_from_text_files(tmp_path,capsys,monkeypatch):
    # 2026-09-20 使用者第 4、5 點：CLI／API 也要能匯入文字檔；詞彙用逗號、頓號、換行或空格分隔都可以
    monkeypatch.setenv('STUDIO_API_TOKEN','test-token')
    glossary=tmp_path/'glossary.txt'; glossary.write_text('彭彭、斯斯\nPICO PARK\n','utf-8')
    outline=tmp_path/'outline.txt'; outline.write_text('本集玩皮可帕克，彭彭主持。','utf-8')
    hints=tmp_path/'hints.txt'; hints.write_text('彭彭 斯斯 海海','utf-8')
    calls=[]
    def handler(request):
        calls.append(request)
        if request.url.path.endswith('/text/keywords'):
            return httpx.Response(200,json={'keywords':['彭彭'],'joined':'彭彭','method':'rules'})
        return httpx.Response(202,json={'id':'job_1','status':'queued'})
    with client(handler) as api:
        assert main(['correct','--project','p','--transcript','tr_1','--provider','local-lmstudio','--glossary-file',str(glossary),
                     '--reference-file',str(outline),'--json'],client=api)==0
        assert main(['correct','--project','p','--transcript','tr_1','--glossary','彭彭 斯斯','--reference','大綱文字','--json'],client=api)==0
        assert main(['analyze','--project','p','--source','s','--profile','quality','--asr-hints-file',str(hints),'--json'],client=api)==0
        assert main(['analyze','--project','p','--source','s','--profile','quality','--asr-model','openrouter','--remote-consent',
                     '--asr-hints','彭彭, 斯斯','--json'],client=api)==0
        assert main(['keywords','--file',str(outline),'--provider','local-lmstudio','--json'],client=api)==0
    first,second,third,fourth=(json.loads(c.content) for c in calls[:4])
    assert first['glossary']==['彭彭','斯斯','PICO PARK'] and first['reference_text']=='本集玩皮可帕克，彭彭主持。'
    assert second['glossary']==['彭彭','斯斯'] and second['reference_text']=='大綱文字'
    assert third['asr_hints']=='彭彭, 斯斯, 海海'  # 空格分隔的檔案整理成逗號分隔送出
    assert fourth['asr_model']=='openrouter' and fourth['remote_consent'] is True and fourth['asr_hints']=='彭彭, 斯斯'
    keywords=calls[4]
    assert keywords.url.path.endswith('/v1/text/keywords')
    assert json.loads(keywords.content)=={'text':'本集玩皮可帕克，彭彭主持。','provider_id':'local-lmstudio','remote_consent':False}


def test_correct_command_passes_the_correction_strength(capsys,monkeypatch):
    """校字強度（保守／逐句改寫）在 CLI 也要送得出去，與網頁同一個欄位。"""
    monkeypatch.setenv('STUDIO_API_TOKEN','test-token')
    seen=[]
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(202,json={'id':'job_c','status':'queued'})
    with client(handler) as api:
        assert main(['correct','--project','p','--transcript','tr_1','--correction-mode','rewrite',
                     '--glossary','彭彭,海海','--json'],client=api)==0
    assert seen[0]['correction_mode']=='rewrite' and seen[0]['glossary']==['彭彭','海海']
    with client(lambda r:(seen.append(json.loads(r.content)),httpx.Response(202,json={'id':'job_c','status':'queued'}))[1]) as api:
        assert main(['correct','--project','p','--transcript','tr_1','--json'],client=api)==0
    assert 'correction_mode' not in seen[1]  # 沒指定就用服務預設


def test_export_without_a_range_covers_the_whole_source(capsys,monkeypatch):
    """M6-2 真跑發現：CLI 匯出沒給 --range 會被契約擋下（網頁會自己算整段）。沒給就補上整段。"""
    monkeypatch.setenv('STUDIO_API_TOKEN','test-token')
    seen=[]
    def handler(request):
        seen.append(request)
        if request.method=='GET' and '/sources' in str(request.url):
            return httpx.Response(200,json={'items':[{'source_id':'s1','duration_us':120000000,'asset_ids':['a1']}]})
        return httpx.Response(202,json={'id':'job_x','status':'queued'})
    with client(handler) as api:
        assert main(['export','--project','p','--source','s1','--transcript','tr_1','--json'],client=api)==0
    body=json.loads(seen[-1].content)
    assert body['kind']=='export' and body['source_id']=='s1'
    assert body['ranges']==[{'start_us':0,'end_us':120000000}]  # 整段
    # 自己有指定就照自己的
    seen.clear()
    with client(handler) as api:
        assert main(['export','--project','p','--source','s1','--transcript','tr_1','--range','0:10-0:20','--json'],client=api)==0
    assert json.loads(seen[-1].content)['ranges']==[{'start_us':10000000,'end_us':20000000}]


def test_source_add_by_local_path_and_subtitle_preview(capsys,monkeypatch,tmp_path):
    """輸出方式對照表補缺：CLI 也能用路徑匯入本機檔（網頁用原生視窗選檔）、也能取字幕預覽（＝匯出的 SRT）。"""
    monkeypatch.setenv('STUDIO_API_TOKEN','test-token')
    seen=[]
    def handler(request):
        seen.append(request)
        url=str(request.url)
        if url.endswith('/sources/local-file'):
            return httpx.Response(201,json={'source_id':'s9','title':'clip.wav'})
        if request.method=='GET' and '/sources' in url:
            return httpx.Response(200,json={'items':[{'source_id':'s9','duration_us':30000000}]})
        if url.endswith('/subtitles/preview'):
            return httpx.Response(200,json={'entries':[{'index':1}],'srt':'1\n00:00:00,000 --> 00:00:01,000\n字幕\n','quality':{'entries':1}})
        return httpx.Response(404,json={})
    with client(handler) as api:
        assert main(['source','add','--project','p','--path','D:/媒體/clip.wav','--json'],client=api)==0
    assert json.loads(seen[0].content)=={'path':'D:/媒體/clip.wav'}
    capsys.readouterr()
    out=tmp_path/'預覽.srt'
    seen.clear()
    with client(handler) as api:
        assert main(['subtitles','preview','--project','p','--source','s9','--transcript','tr_1','--sentences','2','--out',str(out),'--json'],client=api)==0
    body=json.loads(seen[-1].content)
    assert body['source_id']=='s9' and body['transcript_revision']=='tr_1' and body['sentences_per_cue']==2
    assert body['ranges']==[{'start_us':0,'end_us':30000000}]  # 沒給範圍就整段，與匯出一致
    assert out.read_text(encoding='utf-8').startswith('1\n00:00:00,000')  # --out 存成 SRT 檔
    assert json.loads(capsys.readouterr().out)['quality']=={'entries':1}


def test_cli_export_waits_for_alignment_like_the_page_and_preview_writes_exact_bytes(capsys,monkeypatch,tmp_path):
    """真跑發現：CLI 預覽會自動用逐詞對齊的時間，但 CLI 匯出沒加 --await-alignment 就退回句子時間 → 預覽≠匯出。
    網頁匯出本來就會等對齊；CLI 預設也要一樣（可用 --sentence-timing 明確改回句子時間）。--out 要逐位元組照存（不轉 CRLF）。"""
    monkeypatch.setenv('STUDIO_API_TOKEN','test-token')
    seen=[]
    def handler(request):
        seen.append(request)
        url=str(request.url)
        if request.method=='GET' and '/sources' in url:
            return httpx.Response(200,json={'items':[{'source_id':'s1','duration_us':10000000}]})
        if url.endswith('/subtitles/preview'):
            return httpx.Response(200,json={'entries':[],'srt':'1\n00:00:00,000 --> 00:00:01,000\n字幕\n\n','quality':{}})
        return httpx.Response(202,json={'id':'job_x','status':'queued'})
    with client(handler) as api:
        assert main(['export','--project','p','--source','s1','--transcript','tr_1','--json'],client=api)==0
    assert json.loads(seen[-1].content)['await_alignment'] is True
    seen.clear()
    with client(handler) as api:
        assert main(['export','--project','p','--source','s1','--transcript','tr_1','--sentence-timing','--json'],client=api)==0
    assert json.loads(seen[-1].content)['await_alignment'] is False
    out=tmp_path/'p.srt'
    with client(handler) as api:
        assert main(['subtitles','preview','--project','p','--source','s1','--transcript','tr_1','--out',str(out),'--json'],client=api)==0
    assert out.read_bytes()=='1\n00:00:00,000 --> 00:00:01,000\n字幕\n\n'.encode('utf-8')  # 不可變成 CRLF


def test_correct_command_sends_the_subtitle_punctuation_setting(capsys,monkeypatch):
    monkeypatch.setenv('STUDIO_API_TOKEN','test-token')
    seen=[]
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(202,json={'id':'job_c','status':'queued'})
    with client(handler) as api:
        assert main(['correct','--project','p','--transcript','tr_1','--keep-punctuation','--json'],client=api)==0
        assert main(['correct','--project','p','--transcript','tr_1','--json'],client=api)==0
    assert seen[0]['keep_punctuation'] is True and seen[1].get('keep_punctuation',False) is False
