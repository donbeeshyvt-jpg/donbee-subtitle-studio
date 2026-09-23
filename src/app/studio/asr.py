"""工作程序內的語音模型介面；原始詞、對齊、選擇性精修各自回傳新資料。"""
from copy import deepcopy
from contextlib import contextmanager
from contextvars import ContextVar
import gc
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import wave

from .domain import new_id, HALLUCINATION_PATTERN
from .store import StudioError


def _device(device):
    if device not in ('auto','cpu','cuda'):
        raise ValueError('INVALID_DEVICE')
    if device=='auto':
        import ctranslate2
        return 'cuda' if ctranslate2.get_cuda_device_count() else 'cpu'
    return device


def _time(value):
    if value is None or isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
        return None
    return round(value*1000000)


def _text(cue):
    return cue.get('accepted_text') or cue.get('normalized_text') or cue.get('raw_text','')


def _detect_language(text,default=None):
    from app.lid import label_cue
    return label_cue(text,prev_lang=default) or 'unknown'


# 中文裡夾的半形標點 → 全形（字幕格式；2026-09-21 OpenRouter nvidia 轉錄回「好好好, 我要開入」「我在哪?」）
_FULL_WIDTH={',':'，','?':'？','!':'！',';':'；',':':'：'}
_HALF_AFTER_CJK=re.compile(r'([㐀-鿿豈-﫿])([,?!;:])[ 	]*')


def _normalize(text,lang):
    if lang=='zh':
        from app.srt_build import to_traditional
        # 轉到不再變為止：OpenCC 斷詞偶爾把「大陆游戏」斷成「陸游」+「戲」，只轉一次會留下「游戲」（最多三次）
        for _ in range(3):
            converted=to_traditional(text)
            if converted==text:
                break
            text=converted
        return _HALF_AFTER_CJK.sub(lambda m:m.group(1)+_FULL_WIDTH[m.group(2)],text)
    return text


def _mixed(text):
    return bool(re.search(r'[\u3040-\u30ff\u4e00-\u9fff]',text) and re.search(r'[A-Za-z]{2,}',text))


def _load_audio(path):
    import whisperx
    return whisperx.load_audio(str(path))


_scoped_models=ContextVar('studio_scoped_asr_models',default=None)


@contextmanager
def _model_session():
    """只在一次精修內共用模型，結束釋放；切換身份先卸載舊模型。"""
    if _scoped_models.get() is not None:
        yield
        return
    cache={}
    token=_scoped_models.set(cache)
    try:
        yield
    finally:
        cache.clear()
        _scoped_models.reset(token)
        gc.collect()


def _vad_safety_decision(audio,duration,retained):
    """能量不是語音分類；未確定但非靜音的資料不可直接被 VAD 丟棄。"""
    if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in (duration,retained)) or duration<=0:
        return {'action':'not_evaluated','reason':'vad_duration_unavailable'}
    if retained>=duration-.25:
        return {'action':'keep_vad','reason':'nearly_all_audio_retained','initial_retained_sec':retained}
    import numpy as np
    if isinstance(audio,(str,Path)):
        from faster_whisper.audio import decode_audio
        samples=decode_audio(str(audio),sampling_rate=16000)
    else:
        samples=np.asarray(audio)
    if samples.ndim!=1 or not np.all(np.isfinite(samples)):
        raise ValueError('INVALID_AUDIO_SAMPLES')
    frame=1600
    count=len(samples)//frame
    energy=[]
    if count:
        energy.extend(np.sqrt(np.mean(samples[:count*frame].reshape(count,frame)**2,axis=1)).tolist())
    tail=samples[count*frame:]
    active=sum(value>=.001 for value in energy)*.1
    if len(tail) and float(np.sqrt(np.mean(tail**2)))>=.001:
        active+=len(tail)/16000
    # 被保留的所有秒數即使都算作非靜音，仍有這麼多未確定資料會被刪除。
    uncertain=max(0,active-retained)
    preserve=uncertain>max(1.0,duration*.02)
    return {'action':'preserve_uncertain_audio' if preserve else 'keep_vad',
            'reason':'energetic_audio_outside_vad_bound' if preserve else 'discarded_audio_consistent_with_silence',
            'energetic_audio_sec':round(active,6),'initial_retained_sec':retained,
            'discarded_energetic_lower_bound_sec':round(uncertain,6),'rms_floor':.001}


def _acquire_engine(model,device):
    """載入 faster-whisper 模型；同一次精修內共用（_model_session），換模型先卸載舊的。回傳 (engine, device, compute, cache)。"""
    from faster_whisper import WhisperModel
    device=_device(device)
    compute='int8_float16' if device=='cuda' else 'int8'
    cache=_scoped_models.get()
    key=(model,device,compute)
    if cache is not None and key in cache:
        return cache[key],device,compute,cache
    if cache is not None and cache:
        cache.clear(); gc.collect()
    engine=WhisperModel(model,device=device,compute_type=compute)
    if cache is not None: cache[key]=engine
    return engine,device,compute,cache


# 沒有時間戳的模型只取文字：口語上限約每秒 8 字，超過「每秒 12 字＋6 字」視為重複迴圈或幻覺（2026-09-19 Breeze-ASR-26 真跑見過每秒 1775 字）
TEXT_ONLY_CHARS_PER_SEC=12


class RefinementRejected(ValueError):
    code='REFINEMENT_OUTPUT_REJECTED'


QWEN_LANGUAGES={'zh':'Chinese','ja':'Japanese','en':'English'}
QWEN_CODES={name:code for code,name in QWEN_LANGUAGES.items()}


def _qwen_batch(pieces,*,model_dir,languages,contexts,device,timeout_sec):
    """在子程序跑 Qwen3-ASR（獨立套件層 .venv-qwen-asr＋主環境 torch）：每句寫成 WAV，一次轉錄；回 [{text, language}]。"""
    import subprocess
    from . import asr_models
    src=Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix='studio-qwen-') as folder:
        folder=Path(folder)
        items=[]
        for index,(piece,language,context) in enumerate(zip(pieces,languages,contexts)):
            path=folder/f'{index:04}.wav'
            _write_wav(path,piece)
            items.append(dict(path=str(path),language=language,context=context or ''))
        spec=folder/'input.json'
        output=folder/'output.json'
        spec.write_text(json.dumps(dict(model=model_dir,device=device,items=items,max_new_tokens=256),ensure_ascii=False),encoding='utf-8')
        env=dict(os.environ)
        env['PYTHONPATH']=os.pathsep.join([str(asr_models.qwen_overlay()),str(src)]+([env['PYTHONPATH']] if env.get('PYTHONPATH') else []))
        env.setdefault('PYTHONUTF8','1')
        try:
            done=subprocess.run([sys.executable,'-m','app.studio.qwen_worker','--input',str(spec),'--output',str(output)],
                env=env,capture_output=True,text=True,timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            raise StudioError('QWEN_ASR_TIMEOUT','Qwen3-ASR 轉錄超過時間上限',409) from None
        if not output.is_file():
            raise StudioError('QWEN_ASR_FAILED','Qwen3-ASR 子程序沒有產生結果：'+(done.stderr or '')[-300:],409)
        outcome=json.loads(output.read_text(encoding='utf-8'))
    if not outcome.get('ok'):
        error=outcome.get('error') or {}
        raise StudioError(error.get('code','QWEN_ASR_FAILED'),'Qwen3-ASR 失敗：'+str(error.get('message',''))[:200],409)
    rows=outcome.get('results') or []
    if len(rows)!=len(pieces):
        raise StudioError('QWEN_ASR_FAILED','Qwen3-ASR 回傳筆數不符',409)
    return rows


def check_text_output(text,seconds,compression=0):
    """只取文字的輸出（本機 Breeze-ASR-26、遠端 OpenRouter）共用的防呆：過長或重複就拒絕，保留草稿。"""
    if len(re.sub(r'\s','',text))>TEXT_ONLY_CHARS_PER_SEC*seconds+6 or (compression or 0)>2.4:
        raise RefinementRejected(f'REFINEMENT_OUTPUT_REJECTED: {len(text)} chars in {seconds:.2f}s, compression {compression or 0:.2f}')


def _transcribe_text(audio,*,model,device='auto',language='zh',hints=None):
    """只取文字（Breeze-ASR-26 這類不預測時間戳的模型）：一句的音訊、不做 VAD、解碼長度依音長設上限；輸出過長或重複就拒絕。"""
    seconds=len(audio)/16000
    engine,device,compute,cache=_acquire_engine(model,device)
    try:
        segments,_info=engine.transcribe(audio,language=language,without_timestamps=True,word_timestamps=False,vad_filter=False,
            beam_size=5,condition_on_previous_text=False,max_new_tokens=max(8,min(440,math.ceil(seconds*15)+10)),
            **({'hotwords':hints} if hints else {}))
        rows=[row for row in segments if row.text.strip()]
    finally:
        del engine
        if cache is None: gc.collect()
    text=' '.join(row.text.strip() for row in rows).strip()
    compression=max((getattr(row,'compression_ratio',None) or 0 for row in rows),default=0)
    check_text_output(text,seconds,compression)
    logprobs=[row.avg_logprob for row in rows if getattr(row,'avg_logprob',None) is not None]
    return dict(text=text,avg_logprob=min(logprobs) if logprobs else None,compression_ratio=compression or None)


def _segment_cue(segment,language_used,flags,offset_us=0):
    """faster-whisper 的一段 → 逐字稿句子（offset_us：這段音訊在整份音訊裡的起點）。空白或時間不合理回 None。"""
    start,end=_time(segment.start),_time(segment.end)
    if start is None or end is None or end<=start or not segment.text.strip():
        return None
    raw=segment.text.strip()
    lang=_detect_language(raw,language_used)
    normalized=_normalize(raw,lang)
    words=[]
    for word in segment.words or []:
        word_start,word_end=_time(word.start),_time(word.end)
        words.append(dict(id=new_id('word'),text=word.word,normalized_text=_normalize(word.word,lang),
            start_us=None if word_start is None else word_start+offset_us,end_us=None if word_end is None else word_end+offset_us,
            score=getattr(word,'probability',None)))
    flags=list(flags)
    logprob=getattr(segment,'avg_logprob',None)
    compression=getattr(segment,'compression_ratio',None)
    if logprob is not None and logprob < -1: flags.append('low_log_probability')
    if compression is not None and compression>2.4: flags.append('repetition')
    if _mixed(raw): flags.append('mixed_language')
    if HALLUCINATION_PATTERN.search(raw): flags.append('possible_hallucination')
    return dict(id=new_id('cue'),start_us=start+offset_us,end_us=end+offset_us,raw_text=raw,normalized_text=normalized,
        accepted_text=normalized,lang=lang,words=words,word_ids=[w['id'] for w in words],alignment_status='segment',
        review_flags=flags,avg_logprob=logprob,compression_ratio=compression)


# 草稿補轉（2026-09-20 R14）：關掉 VAD 的整檔轉錄偶爾整段跳過有人說話的地方（EP7 turbo 草稿 29.6～40.8 秒沒有字，
# 同一段音訊另一次草稿卻有）。3 秒以上、有聲音的空檔單獨再轉一次。
GAP_FILL_MIN_US=3_000_000
GAP_FILL_RMS=.01          # 每 0.1 秒的 RMS 門檻（約 -40 dBFS）
GAP_FILL_ACTIVE_SEC=.8    # 空檔裡至少這麼多秒有聲音才補轉


def _active_seconds(samples):
    import numpy as np
    frame=1600
    count=len(samples)//frame
    if not count:
        return 0.0
    rms=np.sqrt(np.mean(np.asarray(samples[:count*frame],dtype=np.float32).reshape(count,frame)**2,axis=1))
    return float((rms>=GAP_FILL_RMS).sum())*.1


def _fill_gaps(engine,audio,cues,options,language,total_us):
    import numpy as np
    summary=dict(gaps_checked=0,cues_added=0,min_gap_sec=GAP_FILL_MIN_US/1000000)
    samples=None if isinstance(audio,(str,Path)) else np.asarray(audio,dtype=np.float32)
    if total_us is None and samples is not None:
        total_us=round(len(samples)/16000*1000000)
    if not total_us:
        return {**summary,'skipped':'duration_unavailable'}
    gaps,cursor=[],0
    for cue in sorted(cues,key=lambda c:c['start_us']):
        if cue['start_us']-cursor>=GAP_FILL_MIN_US:
            gaps.append((cursor,cue['start_us']))
        cursor=max(cursor,cue['end_us'])
    if total_us-cursor>=GAP_FILL_MIN_US:
        gaps.append((cursor,total_us))
    if not gaps:
        return summary
    if samples is None:
        try:
            from faster_whisper.audio import decode_audio
            samples=decode_audio(str(audio),sampling_rate=16000)
        except Exception:
            return {**summary,'skipped':'audio_unavailable'}
    gap_options={k:v for k,v in options.items() if k not in ('multilingual','language_detection_segments')}
    gap_options['vad_filter']=False
    if language:
        gap_options['language']=language
    added=[]
    for start_us,end_us in gaps:
        clip=samples[start_us*16000//1000000:end_us*16000//1000000]
        if _active_seconds(clip)<GAP_FILL_ACTIVE_SEC:
            continue  # 真的沒聲音：不補轉，也不給模型機會幻聽
        summary['gaps_checked']+=1
        found,_info=engine.transcribe(clip,**gap_options)
        for segment in found:
            logprob=getattr(segment,'avg_logprob',None)
            compression=getattr(segment,'compression_ratio',None)
            # 補轉的段落本來就被第一趟略過：只收有把握的（像說話、不重複、不是字幕署名那類幻聽）
            if ((getattr(segment,'no_speech_prob',0) or 0)>=.6 or (logprob is not None and logprob<-1)
                    or (compression is not None and compression>2.4) or HALLUCINATION_PATTERN.search(segment.text or '')):
                continue
            cue=_segment_cue(segment,language,['draft_gap_filled'],offset_us=start_us)
            if cue is None:
                continue
            cue['start_us'],cue['end_us']=max(cue['start_us'],start_us),min(cue['end_us'],end_us)
            if cue['end_us']>cue['start_us']:
                added.append(cue)
    cues.extend(added)
    cues.sort(key=lambda c:c['start_us'])
    summary['cues_added']=len(added)
    return summary


def _text_of(cue):
    return cue.get('accepted_text') or cue.get('normalized_text') or cue.get('raw_text') or cue.get('text') or ''


def _bare_text(text):
    return ''.join(ch for ch in (text or '') if ch.isalnum())


def _trim_context_words(row,low,high,previous_text='',next_text=''):
    """精修送了前後文（各約 0.3 秒）：中點落在目標句範圍 [low, high) 之外的字，只有在它們就是鄰句的字時才去掉
    （開頭那串字是前一句的結尾、結尾那串字是下一句的開頭 → 邊界重複）。草稿句界本身不準，鄰句沒有這些字就是本句的字，保留
    （2026-09-20 同一草稿比對：只看時間會把「確實」切成「實」、「紅毛」切成「毛」）。
    回傳去掉的字數；全部被去掉回 None。沒有逐字時間的（只取文字的模型）不動，回 0。"""
    words=row.get('words') or []
    if not words or any(not isinstance(w.get('start_us'),int) or not isinstance(w.get('end_us'),int) for w in words):
        return 0
    inside=[i for i,w in enumerate(words) if low<=(w['start_us']+w['end_us'])//2<high]
    first,last=(inside[0],inside[-1]) if inside else (len(words),len(words)-1)
    leading,trailing=words[:first],words[last+1:] if inside else []
    before,after=_bare_text(previous_text),_bare_text(next_text)

    def repeats(sequence,edge,at_end):
        # 這串字的任一段（貼著目標句那一側）與鄰句相接的那一側相同，就視為鄰句的字
        for size in range(len(sequence),0,-1):
            part=sequence[len(sequence)-size:] if at_end else sequence[:size]
            text=_bare_text(''.join(w.get('text') or '' for w in part))
            if text and edge and (edge.endswith(text) if at_end else edge.startswith(text)):
                return True
        return False
    drop_leading=bool(leading) and repeats(leading,before,True)
    drop_trailing=bool(trailing) and repeats(trailing,after,False)
    kept=words[len(leading) if drop_leading else 0:len(words)-(len(trailing) if drop_trailing else 0)]
    if len(kept)==len(words):
        return 0
    if not kept:
        return None
    text=''.join(w.get('text') or '' for w in kept).strip()
    if not text:
        return None
    row['raw_text']=text
    row['normalized_text']=row['accepted_text']=_normalize(text,row.get('lang'))
    row['words']=kept
    row['word_ids']=[w['id'] for w in kept if w.get('id')]
    row['start_us']=min(w['start_us'] for w in kept)
    row['end_us']=max(w['end_us'] for w in kept)
    return len(words)-len(kept)


def _transcribe_audio(audio,*,model='turbo',device='auto',progress=lambda **kwargs:None,language_policy='auto_ja_zh_en',language_hint=None,hints=None,
                      fill_gaps=False):
    allowed=('ja','zh','en')
    if language_policy not in (*allowed,'auto_ja_zh_en'):
        raise ValueError('INVALID_LANGUAGE_POLICY')
    engine,device,compute,cache=_acquire_engine(model,device)
    try:
        input_audio=str(audio) if isinstance(audio,(str,Path)) else audio
        options=dict(word_timestamps=True,vad_filter=True,beam_size=5,condition_on_previous_text=False)
        if hints:
            options['hotwords']=hints  # 轉錄術語提示（2026-09-20）：faster-whisper 把它放進提示，讓專有名詞照這個寫法
        if language_policy in allowed:
            options['language']=language_policy
        else:
            # 混語音檔（例如開頭日文歌、之後中文）：逐段重新偵測語言，檔案語言用前三個 30 秒段落判定而非只看開頭
            options.update(multilingual=True,language_detection_segments=3)
        segments,info=engine.transcribe(input_audio,**options)
        guard=_vad_safety_decision(input_audio,getattr(info,'duration',None),getattr(info,'duration_after_vad',None))
        if guard['action']=='preserve_uncertain_audio':
            guard['initial_detected_language']=getattr(info,'language',None)
            if hasattr(segments,'close'): segments.close()
            options['vad_filter']=False
            segments,info=engine.transcribe(input_audio,**options)
        detected=getattr(info,'language',None) if language_policy=='auto_ja_zh_en' else None
        language_used=language_policy if language_policy in allowed else detected
        fallback_reason=None
        candidate_probabilities={}
        if language_policy=='auto_ja_zh_en' and detected not in allowed:
            for pair in getattr(info,'all_language_probs',None) or []:
                if not isinstance(pair,(list,tuple)) or len(pair)!=2:
                    continue
                lang,probability=pair
                if lang in allowed and isinstance(probability,(int,float)) and not isinstance(probability,bool) and math.isfinite(probability) and 0<probability<=1:
                    candidate_probabilities[lang]=probability
            if language_hint in allowed:
                language_used=language_hint
                fallback_reason='outside_policy_context_hint'
            elif candidate_probabilities:
                language_used=max(candidate_probabilities,key=candidate_probabilities.get)
                fallback_reason='outside_policy_probability'
            else:
                if hasattr(segments,'close'): segments.close()
                raise ValueError('UNSUPPORTED_DETECTED_LANGUAGE: '+str(detected))
            # 初次延遲生成器尚未消耗；只重試一次，不保留錯誤語言的初稿。
            if hasattr(segments,'close'): segments.close()
            segments,info=engine.transcribe(input_audio,**{k:v for k,v in options.items() if k not in ('multilingual','language_detection_segments')},language=language_used)
            if getattr(info,'language',language_used)!=language_used:
                if hasattr(segments,'close'): segments.close()
                raise ValueError('UNSUPPORTED_DETECTED_LANGUAGE: forced language mismatch')
        cues=[]
        base_flags=['vad_uncertain_audio_preserved'] if guard['action']=='preserve_uncertain_audio' else []
        for segment in segments:
            cue=_segment_cue(segment,language_used,base_flags)
            if cue is None:
                continue
            cues.append(cue)
            progress(completed_audio_us=cue['end_us'],total_audio_us=_time(info.duration))
        gap_fill=_fill_gaps(engine,input_audio,cues,options,language_used,_time(getattr(info,'duration',None))) if fill_gaps else None
        return dict(cues=cues,settings=dict(engine='faster-whisper',model=model,device=device,compute_type=compute,
            language=language_used,language_policy=language_policy,detected_language=detected,language_used=language_used,
            language_fallback_reason=fallback_reason,language_hint=language_hint if language_hint in allowed else None,
            allowed_language_probabilities=candidate_probabilities,cue_language_method='text_detection',word_timestamps='asr_estimated',
            vad_filter=options['vad_filter'],vad_guard=guard,audio_duration_us=_time(getattr(info,'duration',None)),
            duration_after_vad_us=_time(getattr(info,'duration_after_vad',None)),music_classification='not_run',
            **({'gap_fill':gap_fill} if gap_fill is not None else {})))
    finally:
        del engine
        if cache is None: gc.collect()


def draft(path:Path,model='turbo',device='auto',progress=lambda **kwargs:None,language_policy='auto_ja_zh_en',language_hint=None,hints=None):
    # 草稿是整份音訊一趟：補轉被跳過的長段落（精修只處理草稿有的句子，草稿漏掉就永遠補不回來）
    return _transcribe_audio(path,model=model,device=device,progress=progress,language_policy=language_policy,language_hint=language_hint,
                             fill_gaps=True,**({'hints':hints} if hints else {}))


def _validate_cues(cues):
    ids=set()
    for cue in cues:
        if not isinstance(cue.get('id'),str) or cue['id'] in ids:
            raise ValueError('INVALID_CUE_ID')
        ids.add(cue['id'])
        start,end=cue.get('start_us'),cue.get('end_us')
        if type(start) is not int or type(end) is not int or not 0<=start<end<=9007199254740991:
            raise ValueError('INVALID_CUE_RANGE')
    return ids


def _union_duration(spans):
    merged=[]
    for start,end in sorted(spans):
        if merged and start<=merged[-1][1]: merged[-1][1]=max(merged[-1][1],end)
        else: merged.append([start,end])
    return sum(end-start for start,end in merged)


def route_refinement(cues,*,ranges=None,cue_ids=None,max_refine_audio_ratio=.15,context_us=300000,source_start_us=None,source_end_us=None):
    identities=_validate_cues(cues)
    if not isinstance(max_refine_audio_ratio,(int,float)) or not 0<=max_refine_audio_ratio<=1 or type(context_us) is not int or context_us<0:
        raise ValueError('INVALID_REFINEMENT_BUDGET')
    requested=set(cue_ids or [])
    if requested-identities: raise ValueError('UNKNOWN_CUE_ID')
    for span in ranges or []:
        if type(span.get('start_us')) is not int or type(span.get('end_us')) is not int or not 0<=span['start_us']<span['end_us']:
            raise ValueError('INVALID_REFINEMENT_RANGE')
    if not cues:
        return dict(selected=[],deferred=[],budget_us=0,processed_audio_us=0,covered_audio_us=0,ratio=max_refine_audio_ratio)
    first=min(c['start_us'] for c in cues) if source_start_us is None else source_start_us
    last=max(c['end_us'] for c in cues) if source_end_us is None else source_end_us
    if first<0 or last<=first: raise ValueError('INVALID_SOURCE_RANGE')
    known_audio=source_start_us is not None and source_end_us is not None
    coverage=last-first if known_audio else _union_duration([(max(first,c['start_us']),min(last,c['end_us'])) for c in cues if c['end_us']>first and c['start_us']<last])
    budget=int(coverage*max_refine_audio_ratio)
    candidates=[]
    explicit=bool(cue_ids or ranges)
    deferred=[]
    for cue in cues:
        manual=cue['id'] in requested or any(cue['start_us']<span['end_us'] and cue['end_us']>span['start_us'] for span in ranges or [])
        flags=list(cue.get('review_flags') or [])
        if cue.get('avg_logprob') is not None and cue['avg_logprob'] < -1: flags.append('low_log_probability')
        if cue.get('compression_ratio') is not None and cue['compression_ratio']>2.4: flags.append('repetition')
        if _mixed(_text(cue)): flags.append('mixed_language')
        reasons=['user_selected'] if manual else list(dict.fromkeys(flag for flag in flags if flag in ('low_log_probability','repetition','mixed_language','uncertain','missing_alignment','possible_hallucination')))
        if explicit and not manual: reasons=[]
        if not reasons:
            deferred.append(dict(cue_ids=[cue['id']],reason='not_selected' if explicit else 'no_quality_trigger'))
            continue
        start,end=max(first,cue['start_us']-context_us),min(last,cue['end_us']+context_us)
        if start>=end:
            deferred.append(dict(cue_ids=[cue['id']],reason='source_unavailable'))
            continue
        candidates.append(dict(cue_ids=[cue['id']],start_us=start,end_us=end,target_start_us=max(first,cue['start_us']),target_end_us=min(last,cue['end_us']),reasons=reasons,status='selected'))
    selected=[]
    used=0
    for item in sorted(candidates,key=lambda item:('user_selected' not in item['reasons'],item['start_us'])):
        cost=item['end_us']-item['start_us']
        # 每個實際處理 chunk 的補邊與重疊都計入預算，不以 union 掩蓋重算成本。
        if used+cost>budget:
            deferred.append(dict(cue_ids=item['cue_ids'],reason='budget_exhausted',required_audio_us=cost))
            continue
        selected.append(item); used+=cost
    return dict(selected=selected,deferred=deferred,budget_us=budget,processed_audio_us=used,covered_audio_us=coverage,
                ratio=max_refine_audio_ratio,budget_basis='provided_audio_span' if known_audio else 'cue_union',requested_ranges=deepcopy(ranges or []),boundary_policy='expand_to_complete_cue_then_pad',rules_version=1)


def _language_parts(cue):
    text=_text(cue)
    lang=cue.get('lang')
    if lang not in ('zh','ja','en'): lang=_detect_language(text)
    if not _mixed(text): return [(lang,text)]
    # 不分配猜測的詞時間；各語言片段由各自強制對齊模型找時間。
    base='ja' if re.search(r'[\u3040-\u30ff]',text) else 'zh'
    return [('en' if re.search(r'[A-Za-z]',part) else base,part) for part in re.findall(r"[A-Za-z][A-Za-z0-9'’_. -]*|[^A-Za-z]+",text) if part.strip()]


def align(cues,path,source_offset_us=0,*,device='auto',cue_ids=None,progress=lambda **kwargs:None,strict=False):
    identities=_validate_cues(cues)
    selected=set(cue_ids) if cue_ids is not None else identities
    if selected-identities: raise ValueError('UNKNOWN_CUE_ID')
    if type(source_offset_us) is not int or source_offset_us<0: raise ValueError('INVALID_SOURCE_OFFSET')
    if not selected: return dict(cues=deepcopy(cues),settings={'engine':'whisperx-align','languages':[]},warnings=[])
    import whisperx
    device=_device(device)
    audio=_load_audio(path)
    duration_us=round(len(audio)/16000*1000000)
    result=deepcopy(cues)
    lookup={cue['id']:cue for cue in result}
    groups={}
    warnings=[]
    for cue in result:
        if cue['id'] not in selected: continue
        if not source_offset_us<=cue['start_us']<cue['end_us']<=source_offset_us+duration_us:
            raise ValueError('ALIGNMENT_AUDIO_RANGE_UNAVAILABLE')
        cue['words']=[]
        cue['alignment_status']='unaligned'
        for part_index,(lang,text) in enumerate(_language_parts(cue)):
            if lang=='unknown':
                warnings.append(dict(cue_id=cue['id'],code='UNKNOWN_ALIGNMENT_LANGUAGE'))
            else:
                groups.setdefault(lang,[]).append((cue['id'],text,part_index))
    completed=0
    models={}
    failed=set()
    for lang,parts in groups.items():
        model=None
        try:
            model,metadata=whisperx.load_align_model(language_code=lang,device=device)
            models[lang]=metadata.get('model_name',metadata.get('type','whisperx-default'))
            for identity,text,part_index in parts:
                cue=lookup[identity]
                segment=dict(start=(cue['start_us']-source_offset_us)/1000000,end=(cue['end_us']-source_offset_us)/1000000,text=text)
                aligned=whisperx.align([segment],model,metadata,audio,device,return_char_alignments=False)
                for row in aligned.get('segments',[]):
                    for word in row.get('words',[]):
                        start,end=_time(word.get('start')),_time(word.get('end'))
                        if start is not None: start+=source_offset_us
                        if end is not None: end+=source_offset_us
                        if start is None or end is None or end<=start or start<cue['start_us'] or end>cue['end_us']:
                            start,end=None,None
                        cue['words'].append(dict(id=new_id('word'),text=word.get('word',word.get('text','')),start_us=start,end_us=end,score=word.get('score'),lang=lang,_part_index=part_index))
                completed+=1
                progress(completed_parts=completed,total_parts=sum(len(group) for group in groups.values()))
        except Exception as error:
            if strict: raise
            for identity,_,_ in parts: failed.add(identity)
            warnings.append(dict(language=lang,code='ALIGNMENT_FAILED',error_type=type(error).__name__))
        finally:
            del model
            gc.collect()
    for cue in result:
        if cue['id'] not in selected: continue
        cue['words'].sort(key=lambda word:word['_part_index'])
        for word in cue['words']: word.pop('_part_index')
        cue['word_ids']=[word['id'] for word in cue['words']]
        known=bool(cue['words']) and all(word['start_us'] is not None and word['end_us'] is not None for word in cue['words'])
        cue['alignment_status']='word' if known and cue['id'] not in failed else 'unaligned'
        cue['alignment_provenance']=dict(engine='whisperx',source_offset_us=source_offset_us,languages=list(dict.fromkeys(lang for lang,_ in _language_parts(cue))))
    return dict(cues=result,settings=dict(engine='whisperx-align',device=device,languages=list(groups),models=models),warnings=warnings)


def _vv_cues(data):
    """把 vv_worker 的 segments 轉為草稿 cue；空結果回傳空清單，由呼叫端決定是否算失敗。"""
    cues=[]
    for item in data.get('segments',[]):
        start,end=_time(item.get('start')),_time(item.get('end'))
        text=(item.get('text') or '').strip()
        if not text or re.fullmatch(r'\[[^\]]+\]',text): continue
        if start is None or end is None or end<=start: continue
        lang=_detect_language(text)
        normalized=_normalize(text,lang)
        cues.append(dict(id=new_id('cue'),start_us=start,end_us=end,raw_text=text,normalized_text=normalized,accepted_text=normalized,
            lang=lang,words=[],word_ids=[],alignment_status='segment',review_flags=['needs_alignment']))
    return cues


def _write_wav(path,audio):
    import numpy as np
    with wave.open(str(path),'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
        stream.writeframes((np.clip(audio,-1,1)*32767).astype('<i2').tobytes())


def _vv_tokens(audio): return min(8192,max(512,math.ceil(len(audio)/16000*20)))


def _vv_settings(tokens): return dict(engine='vibevoice',model='microsoft/VibeVoice-ASR',quantization='4bit',max_new_tokens=tokens)


def _run_vv_worker(root,entry,output,tokens,timeout_sec):
    """啟動一個 app.vv_worker 子程序（單段 WAV 或 .batch.json 清單）；逾時或取消時整個程序樹一起結束。"""
    from .process import ManagedProcess
    source_root=Path(__file__).resolve().parents[2]
    env=dict(os.environ,PYTHONPATH=str(source_root))
    process=ManagedProcess([sys.executable,'-m','app.vv_worker',str(entry),str(output),str(tokens)],source_root,root/'worker.log',env)
    exit_code=process.wait(timeout_sec)
    if not output.is_file(): raise RuntimeError('VIBEVOICE_WORKER_FAILED')
    data=json.loads(output.read_text(encoding='utf-8'))
    if data.get('error'):
        from .store import StudioError
        error=data['error']
        raise StudioError(error.get('code','VIBEVOICE_WORKER_FAILED'),error.get('message','VibeVoice 執行失敗'),409)
    if exit_code!=0: raise RuntimeError('VIBEVOICE_WORKER_FAILED')
    return data


def _vibevoice(audio,timeout_sec):
    with tempfile.TemporaryDirectory(prefix='studio-vv-') as directory:
        root=Path(directory)
        wav=root/'audio.wav'; output=root/'result.json'
        _write_wav(wav,audio)
        tokens=_vv_tokens(audio)
        cues=_vv_cues(_run_vv_worker(root,wav,output,tokens,timeout_sec))
        if not cues: raise ValueError('EMPTY_VIBEVOICE_OUTPUT')
        return dict(cues=cues,settings=_vv_settings(tokens))


VIBEVOICE_BATCH_MAX_PIECES=8
VIBEVOICE_BATCH_MAX_SECONDS=180


def _vibevoice_batch(pieces,timeout_sec):
    """一批最多 8 段、合計 180 秒，只啟動一個 vv_worker 子程序（.batch.json 入口）；結果依輸入順序回傳。"""
    if not 1<=len(pieces)<=VIBEVOICE_BATCH_MAX_PIECES: raise ValueError('INVALID_VIBEVOICE_BATCH')
    with tempfile.TemporaryDirectory(prefix='studio-vv-batch-') as directory:
        root=Path(directory)
        rows=[]
        for index,audio in enumerate(pieces):
            wav=root/f'piece-{index}.wav'
            _write_wav(wav,audio)
            rows.append(dict(wav=str(wav),max_new_tokens=_vv_tokens(audio)))
        manifest=root/'pieces.batch.json'; output=root/'result.json'
        manifest.write_text(json.dumps(rows),encoding='utf-8')
        data=_run_vv_worker(root,manifest,output,max(row['max_new_tokens'] for row in rows),timeout_sec)
        results=data.get('results')
        if not isinstance(results,list) or len(results)!=len(pieces): raise ValueError('INVALID_VIBEVOICE_BATCH_RESULT')
        return [dict(cues=_vv_cues(item),settings=_vv_settings(row['max_new_tokens'])) for item,row in zip(results,rows)]


def _vibevoice_batches(pieces):
    """依段數上限與合計秒數切批，回傳各批的索引清單；單段超長會自成一批，由呼叫端判定。"""
    batches=[]; current=[]; seconds=0.0
    for index,audio in enumerate(pieces):
        duration=len(audio)/16000
        if current and (len(current)>=VIBEVOICE_BATCH_MAX_PIECES or seconds+duration>VIBEVOICE_BATCH_MAX_SECONDS):
            batches.append(current); current=[]; seconds=0.0
        current.append(index); seconds+=duration
    if current: batches.append(current)
    return batches


def _vibevoice_outcomes(pieces,deadline):
    """分批提交 VibeVoice：每批前檢查 deadline；整批失敗時同批全標失敗，且同一次工作不再重啟已失敗的模型。"""
    outcomes={}
    failure=None
    for number,batch in enumerate(_vibevoice_batches(pieces)):
        remaining=int(deadline-time.monotonic())
        if remaining<=0:
            for index in batch: outcomes[index]=('deadline',None,number)
            continue
        if failure is not None:
            for index in batch: outcomes[index]=('error',failure,number)
            continue
        if len(batch)==1 and len(pieces[batch[0]])/16000>VIBEVOICE_BATCH_MAX_SECONDS:
            outcomes[batch[0]]=('error',ValueError('VIBEVOICE_SPAN_TOO_LONG'),number)
            continue
        try:
            updates=_vibevoice_batch([pieces[index] for index in batch],max(1,remaining))
            for index,update in zip(batch,updates): outcomes[index]=('ok',update,number)
        except Exception as error:
            failure=error
            for index in batch: outcomes[index]=('error',error,number)
    return outcomes


def _span_language_hint(cues):
    """一個精修段落的多數語言（zh／ja／en），沒有可用語言時回 None；讓精修沿用草稿判定，不逐段重新猜。"""
    counts={}
    for cue in cues:
        lang=cue.get('lang')
        if lang in ('zh','ja','en'): counts[lang]=counts.get(lang,0)+1
    if not counts: return None
    return max(sorted(counts),key=lambda lang:counts[lang])


@_model_session()
def refine(cues,path,engine='whisperx',*,source_offset_us=0,ranges=None,cue_ids=None,max_refine_audio_ratio=.15,context_us=300000,
           model='large-v3',device='auto',timeout_sec=1800,fallback_engine=None,language_policy='auto_ja_zh_en',progress=lambda **kwargs:None,remote=None,hints=None):
    if engine not in ('whisperx','vibevoice') or fallback_engine not in (None,'whisperx'):
        raise ValueError('UNSUPPORTED_REFINEMENT_ENGINE')
    if type(source_offset_us) is not int or source_offset_us<0: raise ValueError('INVALID_SOURCE_OFFSET')
    if type(timeout_sec) is not int or timeout_sec<=0: raise ValueError('INVALID_TIMEOUT')
    _validate_cues(cues)
    # 精修模型：內建名稱（large-v3）或模型清單 ct2 類（Breeze-ASR-26）。沒裝好就在載入任何東西之前回清楚的錯誤。
    # 只處理特定語言的模型（Breeze＝中文）：其他語言的段落交給 large-v3，並固定用該模型的語言，避免自動偵測漂移。
    from . import asr_models
    only=asr_models.languages(model)
    if asr_models.is_remote(model) and not remote:
        raise StudioError('MODEL_NOT_INSTALLED',f'尚未設定 {asr_models.source_label(model)} 金鑰或未開啟遠端服務，無法用遠端轉錄',409,{'model':model})
    if remote:
        # 清單鍵指定的轉錄模型（例如 openrouter:microsoft/mai-transcribe-2）：送出時就用這個名稱
        from .remote_asr import transcription_model
        remote=dict(remote,provider=dict(remote['provider'],transcription_model=remote.get('model') or transcription_model(remote['provider'])))
    paths={model:asr_models.resolve(model)}
    # 遠端轉錄每一句回的 usage（秒數、費用）加總進結果（2026-09-21：以前沒記，轉錄花了多少只能看帳戶）
    remote_usage={}
    if only: paths.setdefault(asr_models.DEFAULT,asr_models.DEFAULT)
    audio=_load_audio(path)
    duration_us=round(len(audio)/16000*1000000)

    def whisper(number,item,piece,span_model,span_policy,hint):
        if asr_models.timestamps(span_model):
            return _transcribe_audio(piece,model=paths[span_model],device=device,language_policy=span_policy,language_hint=hint,
                                     **({'hints':hints} if hints else {}))
        # 沒有時間戳的模型：只解碼這一句自己的音訊（不加前後文，避免吃到鄰句），時間沿用草稿句界；逐詞時間交給之後的對齊
        start=(item['target_start_us']-source_offset_us)*16000//1000000
        end=(item['target_end_us']-source_offset_us)*16000//1000000
        if asr_models.runner(span_model)=='qwen':
            # Qwen3-ASR：整批已在子程序轉好（qwen_outcomes），這裡只取這一句的文字
            if isinstance(qwen_outcomes.get('error'),Exception): raise qwen_outcomes['error']
            row=qwen_outcomes[number]
            lang=QWEN_CODES.get(row.get('language_used'))
            found=dict(text=(row.get('text') or '').strip(),avg_logprob=None,compression_ratio=None)
            check_text_output(found['text'],(item['target_end_us']-item['target_start_us'])/1000000)
            lang=lang or _detect_language(found['text'])
        elif asr_models.is_remote(span_model):
            # 遠端轉錄（OpenRouter、ElevenLabs）：各語言都處理；語言沿用草稿對這一段的判定，沒有就讓服務自動偵測
            from . import remote_asr
            lang=span_policy if span_policy in ('zh','ja','en') else hint if hint in ('zh','ja','en') else None
            found=remote_asr.transcribe_text(audio[start:end],provider=remote['provider'],secret=remote['secret'],language=lang)
            remote_usage['requests']=remote_usage.get('requests',0)+1
            for key,value in (found.get('usage') or {}).items():
                if type(value) in (int,float) and value>=0:
                    remote_usage[key]=round(remote_usage.get(key,0)+value,10)
            lang=lang or _detect_language(found['text'])
        else:
            lang=span_policy if span_policy in ('zh','ja','en') else 'zh'
            found=_transcribe_text(audio[start:end],model=paths[span_model],device=device,language=lang,**({'hints':hints} if hints else {}))
        normalized=_normalize(found['text'],lang)
        rows=[dict(id=new_id('cue'),start_us=item['target_start_us']-item['start_us'],end_us=item['target_end_us']-item['start_us'],
            raw_text=found['text'],normalized_text=normalized,accepted_text=normalized,lang=lang,words=[],word_ids=[],
            alignment_status='segment',review_flags=[],avg_logprob=found['avg_logprob'],compression_ratio=found['compression_ratio'])] if found['text'] else []
        return dict(cues=rows,settings=dict(engine='faster-whisper',timing='draft_cue'))

    routing=route_refinement(cues,ranges=ranges,cue_ids=cue_ids,max_refine_audio_ratio=max_refine_audio_ratio,context_us=context_us,
        source_start_us=source_offset_us,source_end_us=source_offset_us+duration_us)
    result=deepcopy(cues)
    warnings=[]
    deadline=time.monotonic()+timeout_sec
    selected=routing['selected']
    pieces=[]
    for item in selected:
        local_start=(item['start_us']-source_offset_us)*16000//1000000
        local_end=(item['end_us']-source_offset_us)*16000//1000000
        pieces.append(audio[local_start:local_end])
    outcomes={}
    if engine=='vibevoice' and selected:
        if _scoped_models.get():
            # VibeVoice 子程序不能與同一工作先前載入的 Whisper 模型共占 GPU。
            _scoped_models.get().clear(); gc.collect()
        outcomes=_vibevoice_outcomes(pieces,deadline)
    plans=[]
    for number,item in enumerate(selected):
        # 精修沿用草稿對這一段的語言判定（多數語言），固定政策時由轉錄端以政策為準；避免短段落自動偵測漂移（例如日文句被寫成中文）
        hint=_span_language_hint([cue for cue in cues if cue['id'] in item['cue_ids']])
        span_model,reason=model,None
        if only and ((language_policy!='auto_ja_zh_en' and language_policy not in only) or (hint is not None and hint not in only)):
            span_model,reason=asr_models.DEFAULT,'language_outside_model'
        plans.append((number,hint,span_model,reason))
    # 同一種模型的段落排在一起：換模型要卸載再載入數 GB 權重，交錯處理會反覆載入
    plans.sort(key=lambda plan:plan[2]!=model)
    qwen_outcomes={}
    qwen_plans=[plan for plan in plans if asr_models.runner(plan[2])=='qwen']
    if qwen_plans:
        # Qwen3-ASR：一次載入、整批轉錄所有句子（每句只送自己的音訊）；失敗時每句都保留草稿
        targets,languages=[],[]
        for number,hint,span_model,reason in qwen_plans:
            item=selected[number]
            start=(item['target_start_us']-source_offset_us)*16000//1000000
            end=(item['target_end_us']-source_offset_us)*16000//1000000
            targets.append(audio[start:end])
            code=language_policy if language_policy in ('zh','ja','en') else hint if hint in ('zh','ja','en') else None
            languages.append(QWEN_LANGUAGES.get(code))
        try:
            rows=_qwen_batch(targets,model_dir=paths[model],languages=languages,contexts=[hints or '']*len(targets),device=device,
                             timeout_sec=max(60,int(deadline-time.monotonic())))
            for (number,*_),language,row in zip(qwen_plans,languages,rows):
                qwen_outcomes[number]=dict(row,language_used=language)
        except Exception as error:
            qwen_outcomes['error']=error
    for done,(number,hint,span_model,reason) in enumerate(plans):
        item=selected[number]
        item['model']=span_model
        if reason: item['model_reason']=reason
        span_policy=only[0] if only and span_model==model else language_policy
        started=time.monotonic()
        kind,payload,batch_number=outcomes.get(number,(None,None,None))
        if batch_number is not None: item['batch_index']=batch_number
        if kind=='deadline' or started>=deadline:
            item['status']='deadline_exhausted'; warnings.append('refinement_deadline_exhausted'); continue
        piece=pieces[number]
        used_engine=engine
        try:
            try:
                if kind=='ok': update=payload
                elif kind=='error': raise payload
                else: update=whisper(number,item,piece,span_model,span_policy,hint)
            except Exception as error:
                if fallback_engine!='whisperx' or engine=='whisperx': raise
                item['fallback_reason']=type(error).__name__
                used_engine='whisperx'
                update=whisper(number,item,piece,span_model,span_policy,hint)
            replacements=[]
            # 鄰句目前的文字（已精修過的用精修後的）：判斷上下文裡的字是不是鄰句的重複
            others=[cue for cue in result if cue['id'] not in item['cue_ids']]
            earlier=[cue for cue in others if cue['end_us']<=item['target_start_us']]
            later=[cue for cue in others if cue['start_us']>=item['target_end_us']]
            neighbours=(_text_of(max(earlier,key=lambda c:c['end_us'])) if earlier else '',
                        _text_of(min(later,key=lambda c:c['start_us'])) if later else '')
            for candidate in update['cues']:
                row=deepcopy(candidate)
                row['start_us']+=item['start_us']; row['end_us']+=item['start_us']
                for word in row.get('words',[]):
                    for key in ('start_us','end_us'):
                        if word.get(key) is not None: word[key]+=item['start_us']
                if not item['target_start_us']<row['end_us'] or row['start_us']>=item['target_end_us']: continue
                # 精修音訊前後多送了上下文：落在上下文裡的字屬於相鄰句，不收（避免相鄰字幕邊界重複）
                trimmed=_trim_context_words(row,item['target_start_us'],item['target_end_us'],*neighbours)
                if trimmed is None: continue
                row['start_us']=max(item['target_start_us'],row['start_us']); row['end_us']=min(item['target_end_us'],row['end_us'])
                row['origin_cue_ids']=item['cue_ids']
                row['refinement_provenance']=dict(engine=used_engine,model=span_model if used_engine=='whisperx' else None,
                    timing='draft_cue' if used_engine=='whisperx' and not asr_models.timestamps(span_model) else 'model',
                    **({'remote_model':__import__('app.studio.remote_asr',fromlist=['transcription_model']).transcription_model(remote['provider'])}
                       if asr_models.is_remote(span_model) and used_engine=='whisperx' else {}),
                    source_span=dict(start_us=item['start_us'],end_us=item['end_us']))
                if trimmed: row['refinement_provenance']['context_words_trimmed']=trimmed
                replacements.append(row)
            if not replacements: raise ValueError('EMPTY_REFINEMENT_OUTPUT')
            # 已接受的手動文字保留，由後續版本選擇決定是否採納精修稿。
            originals=[cue for cue in result if cue['id'] in item['cue_ids']]
            if any(cue.get('edited') for cue in originals):
                item['status']='proposed'; item['replacement_cues']=replacements
            else:
                result=[cue for cue in result if cue['id'] not in item['cue_ids']]+replacements
                item['status']='completed'
            item['engine']=used_engine
        except Exception as error:
            item['status']='failed'; item['error_type']=type(error).__name__; warnings.append('refinement_failed_original_preserved')
            if hasattr(error, 'code'):
                item['error']={'code':error.code,'message':str(error)}
            elif type(error).__name__=='ProviderError':
                # 遠端轉錄的穩定錯誤碼（例如 PROVIDER_ATTESTATION_REQUIRED）；以前只記 error_type，看不出原因
                item['error']={'code':str(error),'message':getattr(error,'detail',None) or str(error),
                               **({'permission':error.permission} if getattr(error,'permission',None) else {}),
                               **({'settings_url':error.settings_url} if getattr(error,'settings_url',None) else {})}
        item['elapsed_sec']=time.monotonic()-started
        progress(completed_spans=done+1,total_spans=len(selected))
    return dict(cues=sorted(result,key=lambda cue:cue['start_us']),routing=routing,settings=dict(engine=engine,model=model,
        device=device,timeout_sec=timeout_sec,fallback_engine=fallback_engine,language_policy=language_policy,music_classification='not_run'),warnings=warnings,
        **({'usage':remote_usage} if remote_usage else {}))
