"""來源字幕取得與比較；來源原稿獨立保存，不自動取代生成字幕。"""

from copy import deepcopy
from difflib import SequenceMatcher
import hashlib
import html
import json
from pathlib import Path
import re
import time
import unicodedata
from urllib.parse import urlsplit
import urllib.error
import urllib.request
from uuid import uuid4

from .media import _run, _yt_args, canonical_youtube_url


class CaptionError(ValueError):
    """穩定錯誤碼，不包含暫時簽章網址。"""


FORMATS=('json3','vtt','srt')
MAX_US=9007199254740991


def caption_tracks(metadata):
    """公開字幕清單：保留語言／來源種類，不公開下載網址。"""
    result=[]
    for field,automatic in (('subtitles',False),('automatic_captions',True)):
        rows=metadata.get(field) or {}
        if not isinstance(rows,dict): raise CaptionError('INVALID_CAPTION_METADATA')
        for language,formats in rows.items():
            if not isinstance(language,str) or not isinstance(formats,list): continue
            available=list(dict.fromkeys(item['ext'] for item in formats if isinstance(item,dict) and item.get('ext') in FORMATS and item.get('url')))
            if not available: continue
            name=next((item.get('name') for item in formats if isinstance(item,dict) and isinstance(item.get('name'),str)),language)
            result.append(dict(id=('automatic:' if automatic else 'manual:')+language,language=language,name=name,
                automatic=automatic,formats=available,provenance='youtube_automatic' if automatic else 'youtube_manual'))
    return sorted(result,key=lambda row:(row['automatic'],row['language']))


def select_track(tracks,language=None,kind='prefer_manual',track_id=None):
    if language=='auto': language=None
    if kind not in ('prefer_manual','manual','automatic'): raise CaptionError('INVALID_CAPTION_KIND')
    if not tracks: raise CaptionError('NO_SOURCE_CAPTIONS')
    candidates=[row for row in tracks if (language is None or row['language']==language)
        and (track_id is None or row['id']==track_id)
        and (kind=='prefer_manual' or row['automatic']==(kind=='automatic'))]
    if not candidates: raise CaptionError('CAPTION_LANGUAGE_UNAVAILABLE')
    preferences=('zh-Hant','zh-TW','zh','zh-Hans','en','ja')
    def rank(row):
        return (row['automatic'] if kind=='prefer_manual' else False,
                preferences.index(row['language']) if row['language'] in preferences else len(preferences),row['language'])
    return deepcopy(min(candidates,key=rank))


def _metadata(url,timeout):
    url=canonical_youtube_url(url)
    if not isinstance(timeout,(int,float)) or not 0<timeout<=600: raise CaptionError('INVALID_CAPTION_TIMEOUT')
    try:
        raw=json.loads(_run([*_yt_args(),'--skip-download','--dump-single-json','--socket-timeout','20','--retries','1',url],timeout))
    except json.JSONDecodeError:
        raise CaptionError('INVALID_CAPTION_METADATA') from None
    if not isinstance(raw,dict): raise CaptionError('INVALID_CAPTION_METADATA')
    if raw.get('is_live') or raw.get('live_status') in ('is_live','is_upcoming'):
        raise CaptionError('LIVE_CAPTIONS_UNSUPPORTED')
    return raw


def probe_captions(url,timeout=60):
    raw=_metadata(url,timeout)
    tracks=caption_tracks(raw)
    return dict(source_platform_id=raw.get('id'),tracks=tracks,available=bool(tracks),
        reason=None if tracks else 'no_source_captions',timebase='source',alignment='unverified_source_timing')


def _validate_caption_url(url):
    parsed=urlsplit(url)
    host=parsed.hostname or ''
    if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in (None,443) or not (host=='youtube.com' or host.endswith('.youtube.com')):
        raise CaptionError('CAPTION_DOWNLOAD_ORIGIN_REJECTED')


class _CaptionRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,request,fp,code,msg,headers,newurl):
        _validate_caption_url(newurl)
        return super().redirect_request(request,fp,code,msg,headers,newurl)


def _fetch_caption(url,headers=None,timeout=60,max_bytes=8*1024*1024):
    _validate_caption_url(url)
    if type(max_bytes) is not int or not 1<=max_bytes<=32*1024*1024: raise CaptionError('INVALID_CAPTION_SIZE_LIMIT')
    if not isinstance(timeout,(int,float)) or not 0<timeout<=600: raise CaptionError('INVALID_CAPTION_TIMEOUT')
    safe_headers={key:value for key,value in (headers or {}).items() if key.lower() in ('user-agent','accept-language') and isinstance(value,str)}
    safe_headers['Accept-Encoding']='identity'
    deadline=time.monotonic()+timeout
    try:
        opener=urllib.request.build_opener(_CaptionRedirect())
        with opener.open(urllib.request.Request(url,headers=safe_headers),timeout=min(timeout,20)) as response:
            if response.headers.get('Content-Encoding','identity') not in ('','identity'):
                raise CaptionError('CAPTION_ENCODING_UNSUPPORTED')
            size=response.headers.get('Content-Length')
            if size and int(size)>max_bytes: raise CaptionError('CAPTION_TOO_LARGE')
            result=bytearray()
            while block:=response.read(min(65536,max_bytes+1-len(result))):
                result.extend(block)
                if len(result)>max_bytes: raise CaptionError('CAPTION_TOO_LARGE')
                if time.monotonic()>=deadline: raise CaptionError('CAPTION_DOWNLOAD_TIMEOUT')
        if not result: raise CaptionError('EMPTY_CAPTION_DOWNLOAD')
        return bytes(result)
    except urllib.error.HTTPError as error:
        raise CaptionError(f'CAPTION_HTTP_{error.code}') from None
    except (urllib.error.URLError,TimeoutError,OSError):
        raise CaptionError('CAPTION_DOWNLOAD_FAILED') from None


def _timestamp(text):
    match=re.fullmatch(r'(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})',text)
    if not match: raise CaptionError('INVALID_CAPTION_TIME')
    hours,minutes,seconds,millis=match.groups()
    if int(minutes)>=60 or int(seconds)>=60: raise CaptionError('INVALID_CAPTION_TIME')
    result=((int(hours or 0)*60+int(minutes))*60+int(seconds))*1000000+int(millis)*1000
    if result>MAX_US: raise CaptionError('INVALID_CAPTION_TIME')
    return result


def _ms(value):
    if value is None: return None
    if type(value) is not int or value<0 or value*1000>MAX_US: raise CaptionError('INVALID_CAPTION_TIME')
    return value*1000


def _clean(text):
    # 去除展示標籤與 VTT karaoke 標記；完整下載原檔另存，不遺失來源證據。
    return html.unescape(re.sub(r'<[^>]*>','',text)).strip()


def _ranges(ranges):
    for span in ranges or []:
        if not isinstance(span,dict) or type(span.get('start_us')) is not int or type(span.get('end_us')) is not int or not 0<=span['start_us']<span['end_us']<=MAX_US:
            raise CaptionError('INVALID_CAPTION_SELECTION')
    return ranges or []


def parse_captions(content,format,language='unknown',*,source_id=None,track_id=None,ranges=None):
    if format not in FORMATS: raise CaptionError('UNSUPPORTED_CAPTION_FORMAT')
    if isinstance(content,bytes):
        try: content=content.decode('utf-8-sig')
        except UnicodeDecodeError: raise CaptionError('INVALID_CAPTION_ENCODING') from None
    if not isinstance(content,str): raise CaptionError('INVALID_CAPTION_CONTENT')
    content=content.lstrip('\ufeff').replace('\r\n','\n').replace('\r','\n')
    selections=_ranges(ranges)
    digest=hashlib.sha256(content.encode('utf-8')).hexdigest()
    cues,unresolved,warnings=[],[],[]
    def append(index,start,end,text,words=None,source_payload=None):
        if not text.strip(): return
        if start is None or end is None:
            unresolved.append(dict(source_index=index,start_us=start,end_us=end,raw_text=text,source_payload=source_payload))
            warnings.append('unknown_event_time'); return
        if not 0<=start<end<=MAX_US: raise CaptionError('INVALID_CAPTION_TIME_RANGE')
        if selections and not any(start<span['end_us'] and end>span['start_us'] for span in selections): return
        identity='sourcecue_'+hashlib.sha256(f'{source_id}:{track_id}:{digest}:{index}'.encode()).hexdigest()[:24]
        for i,word in enumerate(words or []): word['id']=identity+f'_word_{i}'
        cues.append(dict(id=identity,source_id=source_id,source_track_id=track_id,start_us=start,end_us=end,raw_text=text,
            accepted_text=text,lang=language,words=words or [],word_ids=[word['id'] for word in words or []],alignment_status='segment',
            source_index=index,source_payload=source_payload,provenance='source_subtitle',review_flags=['source_timing_unverified']))
    if format=='json3':
        try: payload=json.loads(content)
        except json.JSONDecodeError: raise CaptionError('INVALID_JSON3') from None
        if not isinstance(payload,dict) or not isinstance(payload.get('events'),list): raise CaptionError('INVALID_JSON3')
        for index,event in enumerate(payload['events']):
            if not isinstance(event,dict): raise CaptionError('INVALID_JSON3_EVENT')
            segments=event.get('segs') or []
            if not isinstance(segments,list): raise CaptionError('INVALID_JSON3_EVENT')
            if any(not isinstance(seg,dict) or not isinstance(seg.get('utf8'),str) for seg in segments):
                raise CaptionError('INVALID_JSON3_SEGMENT')
            text=''.join(seg.get('utf8','') for seg in segments if isinstance(seg,dict))
            if not text.strip(): continue
            start,duration=_ms(event.get('tStartMs')),_ms(event.get('dDurationMs'))
            end=start+duration if start is not None and duration is not None and duration>0 else None
            words=[]
            for seg in segments:
                if not isinstance(seg,dict) or not isinstance(seg.get('utf8'),str): raise CaptionError('INVALID_JSON3_SEGMENT')
                offset=_ms(seg.get('tOffsetMs'))
                known=start+offset if start is not None and offset is not None else None
                if known is not None and end is not None and known>=end: known=None
                if seg['utf8'].strip(): words.append(dict(text=seg['utf8'],start_us=known,end_us=None,score=None))
            append(index,start,end,_clean(text),words,deepcopy(event))
    else:
        if format=='vtt' and not content.startswith('WEBVTT'): raise CaptionError('INVALID_WEBVTT_HEADER')
        if 'X-TIMESTAMP-MAP=' in content: raise CaptionError('TRANSPORT_TIME_MAP_UNVERIFIED')
        blocks=re.split(r'\n\s*\n',content.strip())
        count=0
        for index,block in enumerate(blocks):
            lines=block.splitlines()
            if not lines: continue
            if format=='vtt' and (lines[0].startswith('WEBVTT') or lines[0].startswith(('NOTE','STYLE','REGION'))): continue
            timing=next((i for i,line in enumerate(lines) if '-->' in line),None)
            if timing is None: raise CaptionError('INVALID_CAPTION_BLOCK')
            match=re.fullmatch(r'\s*(\S+)\s+-->\s+(\S+)(?:\s+.*)?',lines[timing])
            if not match: raise CaptionError('INVALID_CAPTION_TIME')
            start,end=(_timestamp(part) for part in match.groups())
            original='\n'.join(lines[timing+1:])
            append(index,start,end,_clean(original),source_payload=original)
            count+=1
        if format=='srt' and not count and content.strip(): raise CaptionError('INVALID_SRT')
    if not cues: warnings.append('no_captions_in_selection' if selections else 'no_caption_cues')
    return dict(cues=cues,unresolved_events=unresolved,warnings=list(dict.fromkeys(warnings)),format=format,
        language=language,timebase='source',source_timing='unverified',requested_ranges=deepcopy(selections))


def acquire_captions(url,output_dir,*,language=None,kind='prefer_manual',track_id=None,ranges=None,source_id=None,timeout=90,max_bytes=8*1024*1024):
    """先以隔離 yt-dlp 取得字幕 metadata，再有界取得所選字幕，不下載影音。"""
    _ranges(ranges)
    if not isinstance(timeout,(int,float)) or not 0<timeout<=14400: raise CaptionError('INVALID_CAPTION_TIMEOUT')
    started=time.monotonic()
    raw=_metadata(url,min(timeout,60))
    track=select_track(caption_tracks(raw),language=language,kind=kind,track_id=track_id)
    field='automatic_captions' if track['automatic'] else 'subtitles'
    entries=raw[field][track['language']]
    selected=next(entry for ext in FORMATS for entry in entries if entry.get('ext')==ext and entry.get('url'))
    remaining=timeout-(time.monotonic()-started)
    if remaining<=0: raise CaptionError('CAPTION_DOWNLOAD_TIMEOUT')
    content=_fetch_caption(selected['url'],headers=selected.get('http_headers',raw.get('http_headers',{})),timeout=min(remaining,60),max_bytes=max_bytes)
    parsed=parse_captions(content,selected['ext'],track['language'],source_id=source_id or raw.get('id'),track_id=track['id'],ranges=ranges)
    folder=Path(output_dir).resolve()/('source-captions-'+uuid4().hex)
    folder.mkdir(parents=True,exist_ok=False)
    path=folder/('original.'+selected['ext'])
    with path.open('xb') as file: file.write(content)
    return dict(**parsed,track=track,raw_path=str(path),sha256=hashlib.sha256(content).hexdigest(),size_bytes=len(content),
        source_platform_id=raw.get('id'),downloaded_media=False,elapsed_sec=time.monotonic()-started,available=bool(parsed['cues']),
        status='ready' if parsed['cues'] else 'unresolved_timing' if parsed['unresolved_events'] else 'no_captions_in_selection')


def _normal(text):
    return ''.join(char.casefold() for char in unicodedata.normalize('NFKC',text) if not char.isspace() and not unicodedata.category(char).startswith('P'))


def _validate(cues):
    ids=set()
    for cue in cues:
        if not isinstance(cue.get('id'),str) or cue['id'] in ids: raise CaptionError('INVALID_COMPARE_CUE_ID')
        ids.add(cue['id'])
        if type(cue.get('start_us')) is not int or type(cue.get('end_us')) is not int or not 0<=cue['start_us']<cue['end_us']<=MAX_US:
            raise CaptionError('INVALID_COMPARE_CUE_TIME')


def _text(cue):
    return cue.get('accepted_text') or cue.get('raw_text') or cue.get('text','')


def _union_length(spans):
    union=[]
    for start,end in sorted(spans):
        if union and start<=union[-1][1]: union[-1][1]=max(end,union[-1][1])
        else: union.append([start,end])
    return sum(end-start for start,end in union)


def _interval_index(cues):
    """Balanced source-time index; subtree end bounds prune unrelated intervals."""
    ordered=sorted(cues,key=lambda cue:cue['start_us'])
    def build(lo,hi):
        if lo>=hi: return None
        mid=(lo+hi)//2
        left,right=build(lo,mid),build(mid+1,hi)
        cue=ordered[mid]
        maximum=max(cue['end_us'],left[3] if left else 0,right[3] if right else 0)
        return cue,left,right,maximum
    return build(0,len(ordered))


def _overlapping(node,start,end):
    if node is None or node[3]<=start: return
    cue,left,right,_=node
    yield from _overlapping(left,start,end)
    if cue['start_us']>=end: return
    if cue['end_us']>start: yield cue
    yield from _overlapping(right,start,end)


def compare_captions(generated,source,*,generated_revision=None,source_revision=None,min_overlap_ratio=0):
    _validate(generated); _validate(source)
    if not isinstance(min_overlap_ratio,(int,float)) or not 0<=min_overlap_ratio<=1: raise CaptionError('INVALID_COMPARE_THRESHOLD')
    comparisons=[]
    matched=set()
    index=_interval_index(source)
    for cue in generated:
        candidates=[]
        intersections=[]
        for original in _overlapping(index,cue['start_us'],cue['end_us']):
            start,end=max(cue['start_us'],original['start_us']),min(cue['end_us'],original['end_us'])
            if start<end and (end-start)/(cue['end_us']-cue['start_us'])>=min_overlap_ratio:
                candidates.append(original); intersections.append((start,end)); matched.add(original['id'])
        candidates.sort(key=lambda item:item['start_us'])
        before=_text(cue)
        after='\n'.join(_text(item) for item in candidates)
        normalized_before,normalized_after=_normal(before),_normal(after)
        similarity=SequenceMatcher(None,normalized_before,normalized_after,autojunk=False).ratio() if candidates else None
        comparisons.append(dict(generated_cue_id=cue['id'],source_cue_ids=[item['id'] for item in candidates],generated_text=before,source_text=after,
            status='source_missing' if not candidates else 'equal_normalized' if normalized_before==normalized_after else 'different',
            text_similarity=similarity,overlap_us=_union_length(intersections),source_start_us=cue['start_us'],source_end_us=cue['end_us']))
    return dict(comparisons=comparisons,unmatched_source_cue_ids=[cue['id'] for cue in source if cue['id'] not in matched],
        generated_revision=generated_revision,source_revision=source_revision,automatic_replacement=False,
        method='time_overlap_and_normalized_text_similarity',quality_claim='neither_source_is_ground_truth',warnings=['source_timing_unverified'])
