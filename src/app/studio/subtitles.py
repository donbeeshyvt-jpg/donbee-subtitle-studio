"""不可變字幕衍生排版與來源時間對映，不呼叫模型。"""

from copy import deepcopy
import re
import unicodedata


def _text(cue):
    return cue.get('text', cue.get('accepted_text') or cue.get('raw_text', ''))


def _word_text(word):
    return word.get('normalized_text', word.get('text', word.get('word', '')))


def _join_words(words):
    text = ''
    for word in words:
        part = _word_text(word)
        if text and part and not text[-1].isspace() and not part[0].isspace():
            # 中日文引擎可能逐字給詞，不在人為拼接時把每個字都加空格。
            if not (unicodedata.east_asian_width(text[-1]) in ('W','F') and unicodedata.east_asian_width(part[0]) in ('W','F')):
                text += ' '
        text += part
    return text


def _display(text, keep):
    if keep:
        return ' '.join(text.split())
    output = []
    for i, char in enumerate(text):
        left = text[i-1] if i else ''
        right = text[i+1] if i+1 < len(text) else ''
        protected = char in ".'-’_/" and left.isascii() and right.isascii() and left.isalnum() and right.isalnum()
        output.append(' ' if unicodedata.category(char).startswith('P') and not protected else char)
    return ' '.join(''.join(output).split())


def _range(start, end):
    if any(isinstance(x, bool) or not isinstance(x, int) or x < 0 or x > 9007199254740991 for x in (start,end)) or end <= start:
        raise ValueError('INVALID_TIME_RANGE')


def _known(word):
    start, end = word.get('start_us'), word.get('end_us')
    return isinstance(start,int) and not isinstance(start,bool) and isinstance(end,int) and not isinstance(end,bool) and 0 <= start < end


def _sentence_end(text):
    return bool(re.search(r'[。！？!?](?:[」』”\"]*)$|(?<!\d)\.(?:[\"”]*)$',text.strip()))


# M4-C1（2026-09-20）分段規則 v2：句內停頓 ≥ 0.8 秒就換一則、每則最長顯示 7 秒、
# 每行寬度上限 36（全形 2、半形 1 → 約 18 個中文字）、最多兩行、切點只在詞邊界。
PAUSE_SPLIT_US = 800000
MAX_DISPLAY_US = 7000000
LINE_UNITS = 36
MAX_LINES = 2


def text_width(text):
    """顯示寬度：全形（中日文）算 2、半形算 1。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ('W','F') else 1 for ch in text)


def wrap_text(text, units=LINE_UNITS, lines=MAX_LINES):
    """把一則字幕折成最多 lines 行、每行不超過 units 寬；有空白時優先在空白處折。"""
    words = text.split(' ')
    rows, current = [], ''
    for index, word in enumerate(words):
        candidate = word if not current else current + ' ' + word
        if current and text_width(candidate) > units:
            rows.append(current)
            current = word
        else:
            current = candidate
        while text_width(current) > units:  # 單一長詞或中文連續字：照寬度硬折
            cut = len(current)
            while cut > 1 and text_width(current[:cut]) > units:
                cut -= 1
            rows.append(current[:cut])
            current = current[cut:]
    if current:
        rows.append(current)
    if len(rows) > lines:  # 超過行數（理論上呼叫端已先切）：把多的併到最後一行
        rows = rows[:lines - 1] + [' '.join(rows[lines - 1:])]
    return '\n'.join(row for row in rows if row)


def _split_words(words, keep_punctuation, max_chars=None):
    """一串詞切成多則：遇到停頓、超過最長顯示時間、超過兩行寬度、或超過 max_chars 字就換一則（只在詞邊界切）。"""
    limit = LINE_UNITS * MAX_LINES
    pieces, current = [], []
    for word in words:
        if current:
            gap = word['start_us'] - current[-1]['end_us'] if _known(word) and _known(current[-1]) else 0
            long_enough = _known(word) and _known(current[0]) and word['end_us'] - current[0]['start_us'] > MAX_DISPLAY_US
            shown = _display(_join_words(current + [word]), keep_punctuation)
            too_wide = text_width(shown) > limit or (max_chars is not None and len(shown) > max_chars)
            if gap >= PAUSE_SPLIT_US or long_enough or too_wide:
                pieces.append(current)
                current = []
        current.append(word)
    if current:
        pieces.append(current)
    return pieces


def layout_cues(cues, sentences_per_cue=1, keep_punctuation=False, max_chars=42, max_gap_us=1000000):
    """依現有句／詞證據排版；缺詞時間只使用原句粗時間並標記。"""
    if sentences_per_cue not in (1,2) or max_chars < 1 or max_gap_us < 0:
        raise ValueError('INVALID_LAYOUT_OPTIONS')
    sentences = []
    for source in cues:
        _range(source['start_us'],source['end_us'])
        words = deepcopy(source.get('words') or [])
        # 只有字詞文本仍與接受文字相符時，才可拿它們重排文字。
        match = _display(''.join(_word_text(w) for w in words),False).replace(' ','') == _display(_text(source),False).replace(' ','')
        groups = []
        if words and match and source.get('alignment_status') != 'stale':
            group = []
            for word in words:
                group.append(word)
                if _sentence_end(_word_text(word)):
                    groups.append(group)
                    group = []
            if group:
                groups.append(group)
        if not groups:
            pieces = re.split(r'(?<=[。！？!?])|(?<=\.)(?=\s+[A-Z])',_text(source))
            pieces = [piece for piece in pieces if piece.strip()]
            if len(pieces)>1:
                # 沒有逐詞時間：句子時間按字數比例分給每一段，不讓兩則字幕共用同一個時間（會變成重疊）
                shown = [(piece, _display(piece,keep_punctuation)) for piece in pieces]
                shown = [(piece, text) for piece, text in shown if text]
                total = sum(len(text) for _piece, text in shown) or 1
                span = source['end_us']-source['start_us']
                cursor = source['start_us']
                for index,(piece, text) in enumerate(shown):
                    row = deepcopy(source)
                    row['words'] = []
                    row['text'] = text
                    row['start_us'] = cursor
                    row['end_us'] = source['end_us'] if index==len(shown)-1 else min(source['end_us'], max(cursor+1000, source['start_us']+round(span*sum(len(x) for _p,x in shown[:index+1])/total)))
                    cursor = row['end_us']
                    row['origin_cue_ids'] = list(source.get('origin_cue_ids') or [source['id']])
                    row['warnings'] = list(source.get('warnings') or [])+['coarse_sentence_boundary','estimated_split_timing']
                    row['alignment_status'] = 'segment'
                    if row['end_us'] > row['start_us']:
                        sentences.append(row)
                continue
            groups = [None]
        for group in groups:
            # 停頓、最長顯示時間與寬度都在這裡切（M4-C1）
            chunks = _split_words(group, keep_punctuation, max_chars) if group else [None]
            for chunk in chunks:
                row = deepcopy(source)
                row['words'] = deepcopy(chunk if chunk is not None else words)
                row['text'] = _display(_join_words(chunk) if chunk else _text(source), keep_punctuation)
                row['origin_cue_ids'] = list(source.get('origin_cue_ids') or [source['id']])
                row['warnings'] = list(source.get('warnings') or [])
                if chunk and all(_known(w) for w in chunk):
                    row['start_us'] = max(source['start_us'],min(w['start_us'] for w in chunk))
                    row['end_us'] = min(source['end_us'],max(w['end_us'] for w in chunk))
                    _range(row['start_us'],row['end_us'])
                elif chunk:
                    row['alignment_status'] = 'segment'
                    row['warnings'].append('unknown_word_time')
                if text_width(row['text']) > LINE_UNITS * MAX_LINES or len(row['text']) > max_chars:
                    row['warnings'].append('long_word' if chunk and len(chunk)==1 else 'unsplit_sentence')
                if row['end_us'] - row['start_us'] > MAX_DISPLAY_US:
                    # 沒有逐詞時間就切不動（例如只換文字的模型）：保留整句並標記，讓匯出清單與介面看得到
                    row['warnings'].append('long_display')
                if row['text']:
                    sentences.append(row)
    result = []
    pending = []
    def flush():
        if not pending:
            return
        row = deepcopy(pending[0])
        row['text'] = ' '.join(c['text'] for c in pending)
        row['start_us'] = min(c['start_us'] for c in pending)
        row['end_us'] = max(c['end_us'] for c in pending)
        row['words'] = [deepcopy(w) for c in pending for w in c['words']]
        row['origin_cue_ids'] = list(dict.fromkeys(i for c in pending for i in c['origin_cue_ids']))
        row['warnings'] = list(dict.fromkeys(w for c in pending for w in c['warnings']))
        if any(c.get('alignment_status') in ('stale','segment','unaligned') for c in pending):
            row['alignment_status'] = 'stale' if any(c.get('alignment_status')=='stale' for c in pending) else 'segment'
        row['id'] = f"layout_{len(result)}_" + '_'.join(row['origin_cue_ids'])
        row['text'] = wrap_text(row['text'])  # 折成最多兩行
        result.append(row)
        pending.clear()
    for row in sentences:
        merged_wide = pending and text_width(' '.join(c['text'] for c in pending + [row])) > LINE_UNITS * MAX_LINES
        merged_long = pending and row['end_us'] - pending[0]['start_us'] > MAX_DISPLAY_US
        if pending and (len(pending)>=sentences_per_cue or row['start_us']-pending[-1]['end_us']>min(max_gap_us,PAUSE_SPLIT_US)
                        or merged_wide or merged_long or row.get('sequence_item_id') != pending[-1].get('sequence_item_id')):
            flush()
        pending.append(row)
    flush()
    return result


# 顯示用最短時間（M4-B3，2026-09-19）：逐詞對齊後常有只顯示 0.04 秒的字幕，看不到；往後延到至少 0.7 秒
MIN_DISPLAY_US = 700000
MIN_GAP_US = 50000  # 延長時與下一則之間至少留的空隙


# 延長後仍短於 0.3 秒（下一則緊接著開始、沒空間延長）：看不清楚，併進相鄰字幕（2026-09-20 R14：SRT 仍有 0.02 秒字幕）
SHORT_MERGE_US = 300000
MERGE_MAX_GAP_US = 1000000  # 只和 1 秒內的相鄰字幕合併
MERGE_MAX_CHARS = 42


def _merge_pair(first, second):
    row = deepcopy(first)
    row['text'] = ' '.join(part for part in (_text(first).strip(), _text(second).strip()) if part)
    row['start_us'] = min(first['start_us'], second['start_us'])
    row['end_us'] = max(first['end_us'], second['end_us'])
    row['words'] = [deepcopy(w) for w in (first.get('words') or []) + (second.get('words') or [])]
    row['origin_cue_ids'] = list(dict.fromkeys((first.get('origin_cue_ids') or [first.get('id')]) + (second.get('origin_cue_ids') or [second.get('id')])))
    row['warnings'] = list(dict.fromkeys((first.get('warnings') or []) + (second.get('warnings') or []) + ['merged_short_cue']))
    return row


def ensure_min_duration(cues, limits=None, min_us=MIN_DISPLAY_US, gap_us=MIN_GAP_US, merge_us=SHORT_MERGE_US, max_chars=MERGE_MAX_CHARS):
    """太短的字幕把結束時間往後延到 min_us；不動開始時間、不壓到下一則（留 gap_us）、不超過所屬片段的輸出結尾
    （limits：sequence_item_id → output_end_us）。延長後仍短於 merge_us 的，併進 1 秒內、同一片段的下一則（沒有才併前一則），
    合併後字數不超過 max_chars；開始時間一律不提早。只改顯示時間，不改逐詞時間，也不改傳入資料。"""
    result = _extend(deepcopy(cues), limits, min_us, gap_us)
    while True:
        for index, cue in enumerate(result):
            if cue['end_us'] - cue['start_us'] >= merge_us:
                continue
            following = result[index + 1] if index + 1 < len(result) else None
            previous = result[index - 1] if index else None

            def fits(other, gap):
                return (other is not None and other.get('sequence_item_id') == cue.get('sequence_item_id') and 0 <= gap <= MERGE_MAX_GAP_US
                        and len(_text(cue)) + 1 + len(_text(other)) <= max_chars)
            if fits(following, following['start_us'] - cue['end_us'] if following else -1):
                result[index:index + 2] = [_merge_pair(cue, following)]
            elif fits(previous, cue['start_us'] - previous['end_us'] if previous else -1):
                result[index - 1:index + 1] = [_merge_pair(previous, cue)]
            else:
                if 'short_display' not in cue.setdefault('warnings', []):
                    cue['warnings'].append('short_display')  # 沒有可合併的鄰居：保留並標記
                continue
            result = _extend(result, limits, min_us, gap_us)
            break
        else:
            return result


def _extend(result, limits, min_us, gap_us):
    starts = sorted(c['start_us'] for c in result)
    for cue in result:
        if cue['end_us'] - cue['start_us'] >= min_us:
            continue
        limit = cue['start_us'] + min_us
        later = [s for s in starts if s > cue['start_us']]
        if later:
            limit = min(limit, later[0] - gap_us)
        item_end = (limits or {}).get(cue.get('sequence_item_id'))
        if item_end is not None:
            limit = min(limit, item_end)
        if limit > cue['end_us']:
            cue['end_us'] = limit
    return result


# M4-C2（2026-09-20）匯出品質檢查門檻：每秒字數超過這個值算太快（中文口語約每秒 5～8 字）
FAST_CHARS_PER_SEC = 12


def _plain(text):
    return ''.join(ch for ch in (text or '') if ch.isalnum())


def quality_report(entries, cues=None):
    """匯出／預覽的字幕品質檢查：不改字幕，只回報問題數量，讓匯出清單與介面看得到。

    - overlaps：與前一則時間重疊
    - short_display／long_display：短於 MIN_DISPLAY_US、長於 MAX_DISPLAY_US
    - fast_entries／max_chars_per_sec：每秒字數
    - adjacent_repeats：前一則結尾與這一則開頭重複 ≥3 字
    - missing_cue_ids：逐字稿有話、卻沒有出現在任何一則字幕裡（例如精修失敗或範圍外）
    """
    rows = list(entries or [])
    speeds, fast, overlaps, repeats = [], 0, 0, 0
    short_display = long_display = 0
    warnings = {}
    for index, cue in enumerate(rows):
        seconds = max((cue['end_us'] - cue['start_us']) / 1000000, .001)
        text = _plain(_text(cue))
        speed = len(text) / seconds
        speeds.append(speed)
        if speed > FAST_CHARS_PER_SEC:
            fast += 1
        if cue['end_us'] - cue['start_us'] < MIN_DISPLAY_US:
            short_display += 1
        if cue['end_us'] - cue['start_us'] > MAX_DISPLAY_US:
            long_display += 1
        for warning in cue.get('warnings') or []:
            warnings[warning] = warnings.get(warning, 0) + 1
        if index:
            previous = rows[index - 1]
            if cue['start_us'] < previous['end_us']:
                overlaps += 1
            before, after = _plain(_text(previous)), text
            if any(len(before) >= n and len(after) >= n and before[-n:] == after[:n] for n in range(3, 8)):
                repeats += 1
    covered = {identity for cue in rows for identity in (cue.get('origin_cue_ids') or [])} | {cue.get('id') for cue in rows}
    # 精修過的句子 id 與字幕帶的 origin_cue_ids 不一定相同：兩邊都比對才不會把整份逐字稿當成漏字
    missing = [cue['id'] for cue in (cues or [])
               if _plain(_text(cue)) and cue.get('id') not in covered and not (set(cue.get('origin_cue_ids') or []) & covered)]
    return {'entries': len(rows), 'overlaps': overlaps, 'short_display': short_display, 'long_display': long_display,
            'fast_entries': fast, 'max_chars_per_sec': round(max(speeds), 1) if speeds else 0,
            'adjacent_repeats': repeats, 'missing_cue_ids': missing[:20], 'missing_cues': len(missing),
            'warnings': warnings}


def plain_mappings(items, grouping='merge', timebase='sequence'):
    """只輸出字幕（不剪影音）時各片段在輸出裡的起訖：與匯出工作相同（merge 接續排、separate 各自從 0；source 用來源時間）。"""
    rows, cursor = [], 0
    for item in items:
        duration = item['end_us'] - item['start_us']
        start = cursor if grouping == 'merge' else 0
        row = {'item_id': item['id'], 'output_start_us': start, 'output_end_us': start + duration}
        if timebase == 'source':
            row.update(output_start_us=item['start_us'], output_end_us=item['end_us'])
        rows.append(row)
        cursor += duration
    return rows


def build(cues, items, mappings, *, timebase='sequence', grouping='merge', require_word=False, sentences_per_cue=1,
          keep_punctuation=False, show_language=False):
    """匯出 SRT／VTT 與網頁預覽共用的字幕計算（2026-09-20 使用者：「SRT 檔案多少就是多少，不要預覽不同步」）：
    map_cues → layout_cues → ensure_min_duration（→ 語言標籤）。回傳每個輸出檔一組字幕（merge 只有一組）。"""
    mapped = map_cues(cues, items, timebase, require_word)
    groups = [[c for c in mapped if c['sequence_item_id'] == item['id']] for item in items] if grouping == 'separate' else [mapped]
    limits = {m['item_id']: m['output_end_us'] for m in mappings}
    result = []
    for group in groups:
        laid = layout_cues(group, sentences_per_cue, keep_punctuation)
        # 太短的字幕延長到可讀、延長不了就併進鄰句（不壓下一則、不超出所屬片段）；只影響顯示時間
        laid = ensure_min_duration(laid, limits)
        if show_language:
            for cue in laid:
                if cue.get('lang') and not cue.get('is_tag'):
                    cue['text'] = f"[{cue['lang']}] {cue['text']}"
        result.append(laid)
    return result


def entry_view(cues, items, mappings):
    """預覽用：每則字幕的 SRT 時間字串（與檔案相同的四捨五入）、文字，以及播放器跳轉用的來源時間。"""
    by_item = {m['item_id']: m for m in mappings}
    starts = {item['id']: item['start_us'] for item in items}
    rows = []
    for index, cue in enumerate(cues, 1):
        mapping = by_item.get(cue.get('sequence_item_id'))
        shift = (starts[cue['sequence_item_id']] - mapping['output_start_us']) if mapping else 0
        rows.append({'index': index, 'start': _stamp((cue['start_us'] + 500) // 1000, ','), 'end': _stamp((cue['end_us'] + 500) // 1000, ','),
                     'start_us': cue['start_us'], 'end_us': cue['end_us'], 'source_start_us': cue['start_us'] + shift,
                     'source_end_us': cue['end_us'] + shift, 'text': _text(cue).replace('\r', ''),
                     'origin_cue_ids': list(cue.get('origin_cue_ids') or []), 'warnings': list(cue.get('warnings') or [])})
    return rows


def map_cues(cues, items, timebase='sequence', require_word=False):
    """按各片段獨立求交集；clip 模式以 sequence_item_id 分組匯出。"""
    if timebase not in ('source','clip','sequence'):
        raise ValueError('INVALID_TIMEBASE')
    output, offset = [], 0
    for item in items:
        if item.get('kind') == 'marker' or item.get('selected') is False:
            continue
        start,end = item['start_us'],item['end_us']
        _range(start,end)
        shift = 0 if timebase=='source' else (offset if timebase=='sequence' else 0)-start
        for source in cues:
            _range(source['start_us'],source['end_us'])
            low,high = max(start,source['start_us']),min(end,source['end_us'])
            if low >= high:
                continue
            words = source.get('words') or []
            valid = source.get('alignment_status')=='word' and bool(words) and all(_known(w) for w in words)
            if require_word and not valid:
                raise ValueError('WORD_ALIGNMENT_REQUIRED')
            row = deepcopy(source)
            row['warnings'] = list(source.get('warnings') or [])
            row['origin_cue_ids'] = list(source.get('origin_cue_ids') or [source['id']])
            row['id'] = f"{item['id']}:{source['id']}"
            row['sequence_item_id'] = item['id']
            row['source_start_us'],row['source_end_us'] = low,high
            row['start_us'],row['end_us'] = low+shift,high+shift
            if valid:
                chosen = [w for w in words if 2*start <= w['start_us']+w['end_us'] < 2*end]
                if not chosen:
                    continue
                row['text'] = _join_words(chosen)
                row['words'] = []
                for word in chosen:
                    clone = deepcopy(word)
                    if word['start_us'] < start or word['end_us'] > end:
                        row['warnings'].append('truncated_word')
                    clone['source_start_us'],clone['source_end_us'] = word['start_us'],word['end_us']
                    clone['start_us'],clone['end_us'] = max(start,word['start_us'])+shift,min(end,word['end_us'])+shift
                    row['words'].append(clone)
                row['start_us'] = max(row['start_us'],min(w['start_us'] for w in row['words']))
                row['end_us'] = min(row['end_us'],max(w['end_us'] for w in row['words']))
            else:
                row['text'] = _text(source)
                row['words'] = []
                row['warnings'].append('coarse_alignment')
                if low != source['start_us'] or high != source['end_us']:
                    row['warnings'].append('partial_cue')
            row['warnings'] = list(dict.fromkeys(row['warnings']))
            output.append(row)
        offset += end-start
    return output


def _stamp(millis, separator):
    hours, remainder = divmod(millis,3600000)
    minutes,remainder = divmod(remainder,60000)
    seconds,ms = divmod(remainder,1000)
    return f'{hours:02}:{minutes:02}:{seconds:02}{separator}{ms:03}'


def _serialize(cues, vtt):
    rows = []
    previous = -1
    for i,cue in enumerate(cues,1):
        start,end = cue['start_us'],cue['end_us']
        _range(start,end)
        if start < previous:
            raise ValueError('NON_MONOTONIC_CUES_SPLIT_CLIP_OUTPUTS')
        previous = start
        first,last = (start+500)//1000,(end+500)//1000
        if last <= first:
            raise ValueError('SUB_MILLISECOND_CUE')
        sep = '.' if vtt else ','
        text = _text(cue).replace('\r','').replace('\n\n','\n')
        rows.append(('' if vtt else f'{i}\n')+f'{_stamp(first,sep)} --> {_stamp(last,sep)}\n{text}')
    return ('WEBVTT\n\n' if vtt else '')+'\n\n'.join(rows)+ ('\n' if rows else '')


def to_srt(cues):
    return _serialize(cues,False)


def to_vtt(cues):
    return _serialize(cues,True)
