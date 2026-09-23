"""HTTP 薄客戶端；所有影音與模型工作由同一 API 排程。"""

import argparse
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from urllib.parse import quote, urlsplit
import webbrowser

import httpx

from .environment import environment_report


class CLIError(ValueError):
    def __init__(self,code,message,exit_code=2,payload=None):
        super().__init__(message)
        self.code,self.exit_code,self.payload=code,exit_code,payload


class Parser(argparse.ArgumentParser):
    def error(self,message):
        raise CLIError('INVALID_ARGUMENTS',message)


def parse_time(value):
    if not re.fullmatch(r'\d+(?::\d+){0,2}(?:\.\d{1,6})?',value):
        raise ValueError('時間須為秒數、MM:SS 或 HH:MM:SS，最多六位小數')
    parts=value.split(':')
    numbers=[Decimal(part) for part in parts]
    if len(parts)>1 and any(number>=60 for number in numbers[1:]):
        raise ValueError('分秒欄位必須小於 60')
    total=Decimal(0)
    for number in numbers:
        total=total*60+number
    result=int(total*1000000)
    if result>9007199254740991:
        raise ValueError('時間超出支援範圍')
    return result


def parse_range(value):
    parts=value.split('-')
    if len(parts)!=2:
        raise ValueError('範圍格式須為開始-結束')
    start,end=map(parse_time,parts)
    if end<=start:
        raise ValueError('結束必須晚於開始')
    return dict(start_us=start,end_us=end)


def _csv(value):
    return [item.strip() for item in value.split(',') if item.strip()]


def _read_text(path):
    return Path(path).read_text(encoding='utf-8-sig')


def _terms(value):
    # 與網頁相同的切詞規則：逗號、頓號、分號、換行或空格分隔都可以（terms.split_terms）
    from .terms import split_terms
    return split_terms(value)


def _asr_model(value):
    # 與 API 同一規則（contracts.JobRequest.asr_model）：本機三種，或遠端 openrouter／elevenlabs[:模型名稱]
    from .contracts import ASR_REMOTE_KEY
    if not re.fullmatch(r'(breeze-asr-25|large-v3|qwen3-asr-1\.7b|'+ASR_REMOTE_KEY+r')',value):
        raise argparse.ArgumentTypeError(f'不認得的精修模型：{value}')
    return value


def _read_pcm16(path):
    """16 kHz 單聲道 16 位元 WAV 直接讀；其他格式用 FFmpeg 轉成同樣的 PCM。"""
    import subprocess
    import wave
    try:
        with wave.open(str(path),'rb') as source:
            if source.getnchannels()==1 and source.getsampwidth()==2 and source.getframerate()==16000:
                return source.readframes(source.getnframes())
    except (wave.Error,EOFError):
        pass
    try:
        done=subprocess.run(['ffmpeg','-v','error','-nostdin','-i',str(path),'-f','s16le','-ac','1','-ar','16000','-'],capture_output=True,timeout=900)
    except (OSError,subprocess.TimeoutExpired):
        raise CLIError('AUDIO_UNREADABLE','讀不到音訊：請給 16 kHz 單聲道 WAV，或安裝 FFmpeg',2) from None
    if done.returncode or not done.stdout:
        raise CLIError('AUDIO_UNREADABLE','FFmpeg 無法轉換這個檔案',2)
    return done.stdout


def _realtime(session,args):
    """M7 交叉測試：把音訊檔當直播一段一段送進即時字幕，量第一個事件、牆鐘時間與伺服器端統計。"""
    data=_read_pcm16(args.file)
    body={'model':args.model,'language':args.language,'step_sec':args.step,'remote_consent':args.remote_consent}
    if args.translate_to: body['translate_to']=args.translate_to
    if args.provider: body['provider_id']=args.provider
    if args.hints: body['hints']=args.hints
    created=session.request('POST','/realtime/sessions',json=body)
    sid=quote(created['session_id'],safe='')
    size=max(2,int(args.chunk*16000)*2)
    started=time.monotonic()
    first,count,chunks=None,0,0
    try:
        for index in range(0,len(data),size):
            if args.pace:
                delay=started+index/32000-time.monotonic()
                if delay>0: time.sleep(delay)
            reply=session.request_raw('POST','/realtime/sessions/'+sid+'/audio',data[index:index+size])
            chunks+=1
            events=reply.get('events',[])
            if events and first is None: first=round(time.monotonic()-started,3)
            count+=len(events)
            for event in events:
                if event.get('type') in ('line','translation'):
                    print(json.dumps(event,ensure_ascii=False),file=sys.stderr)
        final=session.request('POST','/realtime/sessions/'+sid+'/finish',timeout=180)
    except BaseException:
        try: session.client.request('DELETE',session.url+'/realtime/sessions/'+sid,headers=session.headers)
        except Exception: pass
        raise
    return {'session_id':created['session_id'],'chunks':chunks,'lines':final.get('lines',[]),'srt':final.get('srt',''),'stats':final.get('stats',{}),
            'events':count+len(final.get('events',[])),
            'client':{'wall_sec':round(time.monotonic()-started,3),'first_event_sec':first,'audio_sec':round(len(data)/32000,3),'paced':bool(args.pace)}}


def _read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _parser():
    root=Parser(prog='python -m app',description='冬比字幕工作室：網頁服務與 API 命令列')
    # 共用選項接受出現在命令前或命令後；子層不覆寫先前已解析的值。
    def common(parser):
        for name in ('api-url','config','idempotency-key'):
            parser.add_argument('--'+name,default=argparse.SUPPRESS)
        for name in ('json','jsonl','wait','cancel-on-interrupt'):
            parser.add_argument('--'+name,action='store_true',default=argparse.SUPPRESS)
        for name,kind in [('wait-timeout',float),('poll-interval',float),('job-timeout',int)]:
            parser.add_argument('--'+name,type=kind,default=argparse.SUPPRESS)
    common(root)
    subs=root.add_subparsers(dest='command',required=True)
    def add(name,parent=subs):
        parser=parent.add_parser(name)
        common(parser)
        return parser
    def scope(parser):
        parser.add_argument('--project',required=True)
    def identity(parser):
        parser.add_argument('id')
    def file_arg(parser):
        parser.add_argument('--file',required=True)
    def job_args(parser,kind):
        scope(parser)
        parser.set_defaults(job_kind=kind)
        for name in ('source','transcript','alignment','sequence','provider','map-revision','base-sequence-revision','glossary-revision'):
            parser.add_argument('--'+name)
        parser.add_argument('--range',action='append',default=[])
        parser.add_argument('--assets',type=_csv)
        parser.add_argument('--cue-ids',type=_csv)
        parser.add_argument('--model')
        parser.add_argument('--engine',choices=['whisperx','vibevoice'])
        parser.add_argument('--profile',choices=['draft','balanced','quality'])
        parser.add_argument('--device',choices=['auto','cpu','cuda'])
        parser.add_argument('--audio-track-id')
        parser.add_argument('--context',type=parse_time)
        parser.add_argument('--music-policy',choices=['conservative','off'])
        parser.add_argument('--language-policy')
        parser.add_argument('--max-refine-audio-ratio',type=float)
    serve=add('serve')
    serve.add_argument('--host',choices=['127.0.0.1','localhost','::1'],default='127.0.0.1')
    serve.add_argument('--port',type=int,default=8765)
    serve.add_argument('--data-dir')
    serve.add_argument('--open-browser',action='store_true')
    live=add('realtime')
    live.add_argument('--file',required=True,help='要模擬直播的音訊（16 kHz 單聲道 16 位元 WAV；其他格式用 FFmpeg 轉）')
    live.add_argument('--model',choices=['turbo','large-v3'],default='turbo')
    live.add_argument('--language',choices=['auto','zh','ja','en'],default='zh')
    live.add_argument('--translate-to',choices=['zh-TW','en','ja'])
    live.add_argument('--provider',help='翻譯用的文字模型')
    live.add_argument('--remote-consent',action='store_true',help='同意把字幕送到遠端文字模型翻譯')
    live.add_argument('--hints',help='術語提示，逗號分隔')
    live.add_argument('--chunk',type=float,default=.5,help='每次送幾秒音訊（預設 0.5）')
    live.add_argument('--step',type=float,default=1.0,help='每收到幾秒新音訊重轉一次（預設 1.0）')
    live.add_argument('--pace',action='store_true',help='照真實時間的速度送（模擬直播）；不加則盡快送')
    keywords=add('keywords')
    source_text=keywords.add_mutually_exclusive_group(required=True)
    source_text.add_argument('--text',help='要拆解的內容')
    source_text.add_argument('--file',help='從文字檔讀要拆解的內容（UTF-8）')
    keywords.add_argument('--provider',help='用哪個文字模型拆解；不指定＝本機規則')
    keywords.add_argument('--remote-consent',action='store_true',help='同意把內容送到遠端文字模型')
    subtitles=add('subtitles')
    subtitle_actions=subtitles.add_subparsers(dest='action',required=True)
    preview=add('preview',subtitle_actions)
    preview.add_argument('--project',required=True)
    preview.add_argument('--source',required=True)
    preview.add_argument('--transcript',required=True)
    preview.add_argument('--alignment')
    preview.add_argument('--range',action='append',default=[],help='只預覽這段（例：0:10-0:40）；不給＝整段')
    preview.add_argument('--sentences',type=int,choices=[1,2],default=1)
    preview.add_argument('--keep-punctuation',action='store_true')
    preview.add_argument('--out',help='把預覽的 SRT 存成檔案（內容與匯出的 SRT 相同）')
    clean=add('clean')
    clean.add_argument('--dry-run',action='store_true',help='只列出會清掉什麼，不真的刪除')
    doctor=add('doctor')
    doctor.add_argument('--probe-providers',action='store_true')
    doctor.add_argument('--env',action='store_true',help='本機環境檢查（Python、FFmpeg、Node、GPU、LM Studio、llama-server、套件、模型、路徑）')
    for group,commands in [('project',['create','list','get','delete']),('source',['add','list','get','upload','probe']),('job',['status','wait','events','cancel','retry','pause','resume','priority','list']),('provider',['list','probe','secret-set','secret-delete']),('models',['status','download']),('artifact',['get','info']),('asset',['get','peaks','keyframes']),('preset',['list','create']),('plan',['create','list','get','run','edits']),('sequence',['get','set','import']),('transcript',['get','edit'])]:
        group_parser=add(group)
        actions=group_parser.add_subparsers(dest='action',required=True)
        for action in commands:
            parser=add(action,actions)
            if group=='project':
                if action=='create': parser.add_argument('--name',required=True)
                elif action=='get': identity(parser)
                elif action=='delete':
                    identity(parser)
                    parser.add_argument('--yes',action='store_true',help='確認刪除（不可復原）')
            elif group=='source':
                scope(parser)
                if action=='add':
                    choose=parser.add_mutually_exclusive_group(required=True)
                    choose.add_argument('--url')
                    choose.add_argument('--root-id')
                    choose.add_argument('--path',help='本機影音檔的完整路徑（不複製、不改原檔；與網頁「匯入本機檔」相同）')
                    parser.add_argument('--relative-path')
                elif action=='upload': file_arg(parser)
                elif action in ('get','probe'): identity(parser)
            elif group=='job':
                if action=='list': scope(parser)
                else: identity(parser)
                if action=='events': parser.add_argument('--after',default='0')
                if action=='priority': parser.add_argument('--value',type=int,required=True)
            elif group=='provider':
                if action in ('probe','secret-set','secret-delete'): identity(parser)
            elif group=='models':
                if action=='download':
                    parser.add_argument('--ids',type=_csv,required=True,help='模型 ID（hf:repo、torch:file、gguf:dir），逗號分隔')
                    parser.add_argument('--confirm',action='store_true',help='確認所需磁碟空間與下載時間後才送出')
            elif group in ('artifact','asset'):
                identity(parser)
                if group=='artifact' and action=='get':
                    parser.add_argument('--out',required=True)
                    parser.add_argument('--overwrite',action='store_true')
            elif group=='preset':
                if action=='create': file_arg(parser)
            elif group=='plan':
                if action=='edits':
                    job_args(parser,'plan_edits')
                    parser.add_argument('--intent',required=True)
                    parser.add_argument('--target-duration',type=parse_time)
                elif action in ('get','run'): identity(parser)
                else:
                    scope(parser)
                    if action=='create': file_arg(parser)
            elif group=='sequence':
                scope(parser)
                if action in ('set','import'): file_arg(parser)
                if action=='import': parser.add_argument('--asset',required=True)
            elif group=='transcript':
                scope(parser); identity(parser)
                if action=='edit': file_arg(parser)
            if action=='list' or (group=='transcript' and action=='get'):
                parser.add_argument('--limit',type=int,default=50)
                parser.add_argument('--cursor',default='0')
    for name in ('download','analyze','refine','align','correct','summarize','export'):
        parser=add(name)
        job_args(parser,'acquire' if name=='download' else name)
        if name=='download':
            parser.add_argument('--audio-only',action='store_true')
            parser.add_argument('--analyze',choices=['draft','balanced','quality'])
            parser.add_argument('--quality',choices=['source','preview'],default='source')
            parser.add_argument('--boundary',choices=['source_seek','accurate'],default='accurate')  # 2026-09-18：source_seek 起點會提前
            parser.add_argument('--container',choices=['source','mp4','mkv','m4a','mp3'])
            parser.add_argument('--max-height',type=int)
            parser.add_argument('--max-fps',type=int)
            parser.add_argument('--audio-bitrate-kbps',type=int)
            parser.add_argument('--allow-transcode',action='store_true')
            parser.add_argument('--output-root')
        elif name=='analyze':
            parser.add_argument('--asr-model',type=_asr_model,
                                help='精修那一趟的模型：large-v3、breeze-asr-25、qwen3-asr-1.7b，或遠端 openrouter／elevenlabs（該來源的主要轉錄模型）、'
                                     'openrouter:模型名稱（例如 openrouter:microsoft/mai-transcribe-2，需先在供應者設定登記）；不指定＝服務預設（Breeze-ASR-25）')
            parser.add_argument('--asr-hints',help='轉錄術語提示（人名、作品名），逗號或空格分隔；送進轉錄模型（faster-whisper hotwords、Qwen context）')
            parser.add_argument('--asr-hints-file',help='從文字檔讀轉錄術語提示（UTF-8）')
            parser.add_argument('--remote-consent',action='store_true',help='同意把音訊送到遠端轉錄（--asr-model openrouter／elevenlabs 時必填）')
        elif name=='correct':
            parser.add_argument('--suggest',action='store_true')
            parser.add_argument('--glossary',type=_terms,help='校字詞彙（人名、作品名），逗號、頓號、換行或空格分隔')
            parser.add_argument('--glossary-file',help='從文字檔讀校字詞彙（UTF-8）')
            parser.add_argument('--reference',help='校字參考資料（故事大綱、角色表），模型只用來判斷詞彙寫法')
            parser.add_argument('--reference-file',help='從文字檔讀校字參考資料（UTF-8，最多 6000 字）')
            parser.add_argument('--apply',action='store_true',help='完成後把高信心修正寫進逐字稿（需 --wait）；低信心留在結果供人工決定')
            parser.add_argument('--keep-punctuation',action='store_true',
                                help='字幕保留標點（預設不保留）：校字的提示詞會依這個字幕格式要求輸出，與網頁的字幕設定同一欄位')
            parser.add_argument('--correction-mode',choices=['conservative','rewrite'],
                                help='校字強度：conservative＝只改明顯辨識錯誤；rewrite＝逐句依上下文改寫（與網頁的「校字強度」同一個欄位）')
        elif name=='summarize':
            parser.add_argument('--outputs',type=_csv,default=['summary','highlights'])
            parser.add_argument('--target-duration',type=parse_time)
        elif name=='export':
            parser.add_argument('--formats',type=_csv,default=['srt'])
            parser.add_argument('--grouping',choices=['separate','merge'],default='merge')
            parser.add_argument('--cut',choices=['copy','accurate'],default='accurate')
            parser.add_argument('--timebase',choices=['source','clip','sequence'])
            parser.add_argument('--sentences',type=int,choices=[1,2],default=1)
            parser.add_argument('--keep-punctuation',action='store_true')
            parser.add_argument('--alignment-policy',choices=['require_word','allow_segment'],default='allow_segment')
            parser.add_argument('--await-alignment',action='store_true',help='（預設就會等；保留這個旗標相容舊指令）匯出字幕前先等這一版逐字稿的逐詞對齊')
            parser.add_argument('--sentence-timing',action='store_true',help='不等逐詞對齊，直接用句子時間匯出（會與網頁／預覽的時間不同）')
            parser.add_argument('--show-language',action='store_true')
    return root


def _job_body(args,ranges=None):
    body={'kind':args.job_kind}
    aliases={'source':'source_id','transcript':'transcript_revision','alignment':'alignment_revision','sequence':'sequence_revision','provider':'provider_id','assets':'asset_ids','context':'context_us'}
    names=list(aliases)+['map_revision','base_sequence_revision','glossary_revision','cue_ids','model','asr_model','engine','profile','device','audio_track_id','music_policy','language_policy','max_refine_audio_ratio']
    for name in names:
        if getattr(args,name,None) is not None:
            body[aliases.get(name,name)]=getattr(args,name)
    if args.range: body['ranges']=[parse_range(value) for value in args.range]
    elif ranges: body['ranges']=ranges
    if hasattr(args,'job_timeout'): body['timeout_sec']=args.job_timeout
    if args.job_kind=='acquire':
        body.update(asset_kind='audio' if args.audio_only else 'video',quality=args.quality,boundary_policy=args.boundary)
        if args.analyze: body['follow_up']={'kind':'analyze','profile':args.analyze}
        policy={name:getattr(args,name) for name in ('container','max_height','max_fps','audio_bitrate_kbps','audio_track_id') if getattr(args,name) is not None}
        if args.allow_transcode: policy['allow_transcode']=True
        if policy: body['format_policy']=policy
        if args.output_root: body['output_root_id']=args.output_root
    if args.job_kind=='correct':
        body['mode']='suggest'
        glossary=list(getattr(args,'glossary',None) or [])
        if getattr(args,'glossary_file',None):
            glossary+=[term for term in _terms(_read_text(args.glossary_file)) if term not in glossary]
        if glossary: body['glossary']=glossary[:100]
        reference=_read_text(args.reference_file) if getattr(args,'reference_file',None) else getattr(args,'reference',None)
        if reference and reference.strip(): body['reference_text']=reference.strip()
        if getattr(args,'correction_mode',None): body['correction_mode']=args.correction_mode
        if getattr(args,'keep_punctuation',False): body['keep_punctuation']=True
    if args.job_kind=='analyze':
        hints=_read_text(args.asr_hints_file) if getattr(args,'asr_hints_file',None) else getattr(args,'asr_hints',None)
        if hints and _terms(hints):
            from .terms import join_terms
            body['asr_hints']=join_terms(_terms(hints))[:2000]
        if getattr(args,'remote_consent',False): body['remote_consent']=True
    if args.job_kind=='summarize':
        body['outputs']=args.outputs
        if args.target_duration: body['target_highlight_duration_us']=args.target_duration
    if args.job_kind=='plan_edits':
        body['intent']=args.intent
        if args.target_duration: body['target_duration_us']=args.target_duration
    if args.job_kind=='export':
        body.update(formats=args.formats,grouping=args.grouping,cut_mode=args.cut,subtitle_timebase=args.timebase or ('clip' if args.grouping=='separate' else 'sequence'),sentences_per_cue=args.sentences,keep_punctuation=args.keep_punctuation,alignment_policy=args.alignment_policy,show_language=args.show_language,
                    # 2026-09-21 真跑：沒等對齊時 CLI 匯出用句子時間，與網頁匯出／預覽（逐詞對齊時間）不同 → 預設等對齊
                    await_alignment=bool(getattr(args,'transcript',None)) and not args.sentence_timing and not getattr(args,'alignment',None))
    # 與伺服器共用輸入契約；不在 CLI 複製模型／影音管線。
    from .contracts import JobRequest
    return JobRequest.model_validate(body).model_dump(mode='json',exclude_unset=True)


class Session:
    def __init__(self,args,client=None):
        from app import config as app_config
        default=Path(app_config.resolve_data_dir())/'config.json'
        path=Path(getattr(args,'config',default))
        config=_read_json(path) if path.exists() else {}
        self.url=getattr(args,'api_url',os.environ.get('STUDIO_API_URL',config.get('api_url','http://127.0.0.1:8765'))).rstrip('/')
        parsed=urlsplit(self.url)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise CLIError('INVALID_API_URL','API 網址無效')
        if not self.url.endswith('/v1'): self.url+='/v1'
        token=os.environ.get('STUDIO_API_TOKEN',config.get('token',''))
        self.headers={'Authorization':'Bearer '+token} if token else {}
        if getattr(args,'idempotency_key',None): self.headers['Idempotency-Key']=args.idempotency_key
        self.client=client or httpx.Client(timeout=30,follow_redirects=False,trust_env=False)
        self.owned=client is None
        self.args=args
        self.active_job=None
    def check(self,response):
        if response.is_success: return
        if not response.is_stream_consumed: response.read()
        try: payload=response.json()
        except ValueError: payload={'error':{'code':f'HTTP_{response.status_code}','message':'API 請求失敗'}}
        code=6 if response.status_code==409 else 2 if 400<=response.status_code<500 else 3
        raise CLIError('API_ERROR','API 請求失敗',code,payload)
    def request(self,method,path,**kwargs):
        response=self.client.request(method,self.url+path,headers=self.headers,**kwargs)
        self.check(response)
        try: return response.json()
        except ValueError: raise CLIError('INVALID_API_RESPONSE','API 未回傳有效 JSON',3) from None
    def request_raw(self,method,path,content):
        response=self.client.request(method,self.url+path,headers={**self.headers,'Content-Type':'application/octet-stream'},content=content)
        self.check(response)
        try: return response.json()
        except ValueError: raise CLIError('INVALID_API_RESPONSE','API 未回傳有效 JSON',3) from None
    def wait(self,job):
        identity=job.get('job_id') or job.get('id')
        if not identity: raise CLIError('MISSING_JOB_ID','API 未回傳工作編號',3)
        self.active_job=identity
        deadline=time.monotonic()+getattr(self.args,'wait_timeout',3600)
        status=job
        while True:
            if status.get('status') in ('succeeded','failed','cancelled','interrupted'):
                return status,{'succeeded':0,'failed':3,'cancelled':4,'interrupted':3}[status['status']]
            if time.monotonic()>=deadline:
                raise CLIError('WAIT_TIMEOUT','客戶端停止等待，後端工作未取消',5,dict(error={'code':'WAIT_TIMEOUT','message':'客戶端停止等待，後端工作未取消'},job_id=identity,job_cancelled=False))
            status=self.request('GET','/jobs/'+quote(identity,safe=''),timeout=min(30,max(.001,deadline-time.monotonic())))
            if status.get('status') not in ('succeeded','failed','cancelled','interrupted'):
                print(json.dumps({'job_id':identity,'status':status.get('status'),'stage':status.get('stage')},ensure_ascii=False),file=sys.stderr)
                time.sleep(min(getattr(self.args,'poll_interval',.5),max(0,deadline-time.monotonic())))
    def result(self,data):
        if getattr(self.args,'wait',False): return self.wait(data)
        return data,0


def _apply_corrections(session,project,args,job):
    """校字完成後：高信心修正一次寫進逐字稿（與網頁「直接套用」相同），低信心與拒絕項留在輸出供人工決定。"""
    result=job.get('result') or {}
    patches=[p for p in result.get('patches',[]) if p.get('replacement_text') and p.get('replacement_text')!=p.get('original_text')]
    high=[p for p in patches if p.get('confidence')!='low']
    low=[p for p in patches if p.get('confidence')=='low']
    base=(high or patches or [{}])[0].get('base_revision') or args.transcript
    summary=dict(job_id=job.get('job_id') or job.get('id'),status=job.get('status'),applied=0,skipped_low_confidence=len(low),
                 rejected=len(result.get('rejected_patches',[])),ignored_no_change=result.get('ignored_no_change',0),
                 transcript_revision=base,patches=high,low_confidence=low)
    if high and base:
        edited=session.request('POST',project+'/transcripts/'+quote(base,safe='')+'/edits',
                               json={'base_revision':base,'origin':'cli','job_id':summary['job_id'],'edits':[{'cue_id':p['cue_id'],'text':p['replacement_text']} for p in high]})
        summary.update(applied=len(high),transcript_revision=edited.get('id') or edited.get('revision'))
    return summary


def _dispatch(args,session):
    command,action=args.command,getattr(args,'action',None)
    esc=lambda value:quote(value,safe='')
    project='/projects/'+esc(args.project) if getattr(args,'project',None) else None
    identity=esc(args.id) if getattr(args,'id',None) else None
    if hasattr(args,'job_kind'):
        if getattr(args,'apply',False) and not getattr(args,'wait',False):
            raise CLIError('INVALID_ARGUMENTS','--apply 需要搭配 --wait（要等校字完成才能套用）',2)
        whole=None
        if args.job_kind=='export' and not args.range and not getattr(args,'sequence',None) and getattr(args,'source',None):
            # 匯出整段：沒給 --range 就問來源時長自己補（網頁的「匯出 SRT」也是送整段範圍）
            listing=session.request('GET',project+'/sources',params={'limit':200})
            info=next((s for s in listing.get('items',[]) if s.get('source_id')==args.source or s.get('id')==args.source),{})
            duration=info.get('duration_us')
            if not isinstance(duration,int) or duration<=0:
                raise CLIError('INVALID_ARGUMENTS','這個來源還沒有可用的時長，請用 --range 指定匯出範圍',2)
            whole=[{'start_us':0,'end_us':duration}]
        status,code=session.result(session.request('POST',project+'/jobs',json=_job_body(args,whole)))
        if getattr(args,'apply',False) and code==0:
            return _apply_corrections(session,project,args,status),0
        return status,code
    if command=='realtime':
        return _realtime(session,args),0
    if command=='keywords':
        text=_read_text(args.file) if args.file else args.text
        return session.request('POST','/text/keywords',json={'text':text.strip(),'provider_id':args.provider,'remote_consent':args.remote_consent}),0
    if command=='subtitles':
        ranges=[parse_range(value) for value in args.range]
        if not ranges:
            listing=session.request('GET',project+'/sources',params={'limit':200})
            info=next((s for s in listing.get('items',[]) if s.get('source_id')==args.source or s.get('id')==args.source),{})
            duration=info.get('duration_us')
            if not isinstance(duration,int) or duration<=0:
                raise CLIError('INVALID_ARGUMENTS','這個來源還沒有可用的時長，請用 --range 指定範圍',2)
            ranges=[{'start_us':0,'end_us':duration}]
        body={'source_id':args.source,'ranges':ranges,'transcript_revision':args.transcript,'sentences_per_cue':args.sentences,
              'keep_punctuation':bool(args.keep_punctuation),'subtitle_timebase':'sequence','grouping':'merge',
              **({'alignment_revision':args.alignment} if args.alignment else {})}
        result=session.request('POST',project+'/subtitles/preview',json=body)
        if args.out:
            Path(args.out).write_bytes(result.get('srt','').encode('utf-8'))  # 逐位元組照存：不讓 Windows 把 LF 轉成 CRLF
        return result,0
    if command=='clean':
        return session.request('POST','/maintenance/cleanup',json={'dry_run':bool(args.dry_run)}),0
    if command=='doctor':
        try:
            result=session.request('GET','/capabilities')
        except (CLIError,httpx.HTTPError) as error:
            # --env：服務連不上（連線錯誤）或回錯誤都照樣做本機環境檢查（TEST_PLAN I09）；沒有 --env 則照舊回報
            if not args.env: raise
            result={'api':'unreachable','error':getattr(error,'code',None) or 'SERVICE_UNREACHABLE'}
        if args.env:
            # 本機直接檢查，不經服務；服務不可達時仍可回報環境
            result['environment']=environment_report()
        if args.probe_providers:
            result['provider_probes']={}
            for provider in session.request('GET','/providers').get('items',[]):
                try: result['provider_probes'][provider['id']]=session.request('POST','/providers/'+esc(provider['id'])+'/probe')
                except CLIError as error: result['provider_probes'][provider['id']]=error.payload or {'error':error.code}
        return result,0
    if command=='project':
        if action=='create': return session.request('POST','/projects',json={'name':args.name}),0
        if action=='delete':
            # 不可復原：一定要 --yes（與網頁的二次確認同一個意思）
            if not args.yes: raise CLIError('CONFIRMATION_REQUIRED','刪除專案不可復原：確定要刪請加上 --yes',2)
            return session.request('DELETE','/projects/'+identity),0
        return session.request('GET','/projects'+('/'+identity if identity else ''),params={'limit':args.limit,'cursor':args.cursor} if action=='list' else None),0
    if command=='source':
        if action=='add':
            if getattr(args,'path',None):
                return session.request('POST',project+'/sources/local-file',json={'path':args.path}),0
            body={'kind':'youtube','url':args.url} if args.url else {'kind':'local','root_id':args.root_id,'relative_path':args.relative_path}
            return session.request('POST',project+'/sources',json=body),0
        if action=='upload':
            with Path(args.file).open('rb') as file:
                return session.result(session.request('POST',project+'/uploads',files={'file':(Path(args.file).name,file)}))
        if action=='probe': return session.result(session.request('POST',project+'/jobs',json={'kind':'probe','source_id':args.id}))
        if action=='get':
            result=session.request('GET',project)
            source=next((s for s in result.get('sources',[]) if s.get('id',s.get('source_id'))==args.id),None)
            if source is None: raise CLIError('NOT_FOUND','找不到此來源',3)
            return source,0
        return session.request('GET',project+'/sources',params={'limit':args.limit,'cursor':args.cursor}),0
    if command=='job':
        if action=='list': return session.request('GET',project+'/jobs',params={'limit':args.limit,'cursor':args.cursor}),0
        path='/jobs/'+identity
        if action=='wait': return session.wait({'id':args.id})
        if action=='status': return session.request('GET',path),0
        if action=='events':
            session.active_job=args.id
            headers={**session.headers,'Last-Event-ID':str(args.after)}
            data=[]
            deadline=time.monotonic()+getattr(args,'wait_timeout',3600)
            with session.client.stream('GET',session.url+path+'/events',headers=headers) as response:
                session.check(response)
                for line in response.iter_lines():
                    if time.monotonic()>deadline: raise CLIError('WAIT_TIMEOUT','事件串流等待逾時',5)
                    if line.startswith('data:'): data.append(line[5:].lstrip())
                    elif not line and data:
                        print(json.dumps(json.loads('\n'.join(data)),ensure_ascii=False),flush=True)
                        data=[]
            return None,0
        if action in ('cancel','retry'): return session.result(session.request('POST',path+'/'+action))
        body={'priority':args.value} if action=='priority' else {'dispatch_paused':action=='pause'}
        if action=='priority' and not 0<=args.value<=100: raise CLIError('INVALID_ARGUMENTS','優先順序須介於 0–100')
        return session.request('PATCH',path+'/control',json=body),0
    if command=='provider':
        if action=='list': return session.request('GET','/providers'),0
        if action=='probe': return session.request('POST','/providers/'+identity+'/probe'),0
        if action=='secret-delete': return session.request('DELETE','/providers/'+identity+'/secret'),0
        # 金鑰只從環境變數 STUDIO_PROVIDER_SECRET 或 stdin 讀取，不接受命令列引數，也不印出
        value=os.environ.get('STUDIO_PROVIDER_SECRET') or sys.stdin.readline().strip()
        if not value: raise CLIError('SECRET_REQUIRED','請以環境變數 STUDIO_PROVIDER_SECRET 或標準輸入提供金鑰')
        return session.request('PUT','/providers/'+identity+'/secret',json={'secret':value}),0
    if command=='models':
        if action=='status': return session.request('GET','/models/status'),0
        if not args.confirm: raise CLIError('CONFIRMATION_REQUIRED','請加 --confirm 確認所需磁碟空間與下載時間後再開始')
        return session.request('POST','/models/download',json={'ids':args.ids,'confirm':True}),0
    if command=='asset': return session.request('GET','/assets/'+identity+('' if action=='get' else '/'+action)),0
    if command=='artifact':
        if action=='info': return session.request('GET','/artifacts/'+identity),0
        output=Path(args.out).resolve()
        if output.exists() and not args.overwrite: raise CLIError('OUTPUT_EXISTS','輸出已存在；使用 --overwrite 才覆寫')
        staging=output.with_name(output.name+'.'+os.urandom(6).hex()+'.part')
        try:
            with session.client.stream('GET',session.url+'/artifacts/'+identity+'/content',headers=session.headers) as response:
                session.check(response)
                with staging.open('xb') as file:
                    for block in response.iter_bytes(): file.write(block)
            if not args.overwrite and output.exists(): raise CLIError('OUTPUT_EXISTS','輸出在下載期間已建立')
            if args.overwrite:
                from .fsutil import replace_with_retry
                replace_with_retry(staging, output)
            else:
                # 同目錄原子建立連結，避免檢查與發布之間另一檔案被覆寫。
                os.link(staging,output)
        finally: staging.unlink(missing_ok=True)
        return {'artifact_id':args.id,'output':str(output),'size_bytes':output.stat().st_size},0
    if command=='preset': return session.request('POST' if action=='create' else 'GET','/presets',**({'json':_read_json(args.file)} if action=='create' else {})),0
    if command=='plan':
        if action=='run': return session.result(session.request('POST','/plans/'+identity+'/run'))
        if action=='get': return session.request('GET','/plans/'+identity),0
        return session.request('POST' if action=='create' else 'GET',project+'/plans',**({'json':_read_json(args.file)} if action=='create' else {})),0
    if command=='sequence':
        if action=='import':
            file=Path(args.file)
            body={'asset_id':args.asset,'format':file.suffix.lstrip('.'),'content':file.read_text(encoding='utf-8-sig')}
            return session.result(session.request('POST',project+'/imports',json=body))
        return session.request('PUT' if action=='set' else 'GET',project+'/sequence',**({'json':_read_json(args.file)} if action=='set' else {})),0
    if command=='transcript':
        path=project+'/transcripts/'+identity
        return session.request('POST' if action=='edit' else 'GET',path+('/edits' if action=='edit' else ''),**({'json':_read_json(args.file)} if action=='edit' else {'params':{'limit':args.limit,'cursor':args.cursor}})),0
    raise CLIError('UNKNOWN_COMMAND','尚無此命令')


def main(argv=None,*,client=None):
    session=None
    try:
        args=_parser().parse_args(argv)
        if getattr(args,'wait_timeout',1)<=0 or getattr(args,'poll_interval',1)<=0:
            raise CLIError('INVALID_ARGUMENTS','等待期限與輪詢間隔必須大於零')
        if args.command=='serve':
            if not 1<=args.port<=65535: raise CLIError('INVALID_ARGUMENTS','連接埠超出範圍')
            from .api import create_app
            from .coordinator import Coordinator
            from app import config as app_config
            data_root=Path(args.data_dir) if args.data_dir else Path(app_config.resolve_data_dir())
            if data_root.is_dir() and not Coordinator.available(data_root):
                raise CLIError(Coordinator.ALREADY_RUNNING[0],Coordinator.ALREADY_RUNNING[1],3)
            import uvicorn
            app=create_app(args.data_dir)
            if args.open_browser:
                host='[::1]' if args.host=='::1' else args.host
                timer=threading.Timer(1,webbrowser.open,args=(f'http://{host}:{args.port}',))
                timer.daemon=True; timer.start()
            uvicorn.run(app,host=args.host,port=args.port)
            return 0
        session=Session(args,client)
        result,code=_dispatch(args,session)
        if result is not None: print(json.dumps(result,ensure_ascii=False,allow_nan=False))
        return code
    except KeyboardInterrupt:
        identity=session.active_job if session else None
        requested=False
        cancelled=False
        if identity and getattr(session.args,'cancel_on_interrupt',False):
            try:
                result=session.request('POST','/jobs/'+quote(identity,safe='')+'/cancel')
                requested=True
                cancelled=result.get('status')=='cancelled'
            except (httpx.HTTPError,CLIError): pass
        print(json.dumps(dict(job_id=identity,job_cancelled=cancelled,cancel_requested=requested,status='client_interrupted'),ensure_ascii=False))
        return 4
    except CLIError as error:
        print(json.dumps(error.payload or {'error':{'code':error.code,'message':str(error)}},ensure_ascii=False))
        return error.exit_code
    except httpx.HTTPError:
        print(json.dumps({'error':{'code':'SERVICE_UNREACHABLE','message':'無法連線服務或請求逾時'}},ensure_ascii=False))
        return 5
    except (ValueError,OSError,InvalidOperation) as error:
        print(json.dumps({'error':{'code':'INVALID_ARGUMENTS','message':str(error)}},ensure_ascii=False))
        return 2
    finally:
        if session and session.owned: session.client.close()
