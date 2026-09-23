"""SRT 組裝：cue 資料模型、+offset、跨區段合併、重編號、寫檔。"""
import os
import json
from copy import deepcopy
from app.reuse import is_tag, fmt

_cc = None
_cc_ready = False
_SENT_END = "。！？!?…"
# 一句一行模式要去掉的「軟標點」（保留 ！？!? 與文字）
_DROP_PUNCT = "。，、；：．・…､｡,;:~～「」『』【】（）()《》〈〉\"'`"


def _strip_soft_punct(text):
    """去掉逗號句號等標點，只保留 ！？!? 與文字內容。"""
    return "".join(ch for ch in (text or "") if ch not in _DROP_PUNCT).strip()


def make_cue(start, end, text, lang=None, words=None):
    """建立 cue dict（絕對秒）。is_tag 標記 [Music]/[唱歌] 這類整段一條的標籤。"""
    return {
        "start": float(start), "end": float(end),
        "text": (text or "").strip(), "lang": lang,
        "is_tag": is_tag(text), "words": words,
    }


def offset_segments(segments, offset):
    """把某區段 ASR 回傳的本地時間 segments 平移 offset 秒 → 絕對時間 cue 清單。"""
    cues = []
    for s in segments:
        local_start = s.get("start") if s.get("start") is not None else 0.0
        local_end = s.get("end") if s.get("end") is not None else local_start
        st = local_start + offset
        en = local_end + offset
        words = s.get("words")
        if words:
            words = [dict(w,
                          start=w["start"] + offset if w.get("start") is not None else None,
                          end=w["end"] + offset if w.get("end") is not None else None) for w in words]
        cues.append(make_cue(st, en, s.get("text", ""), s.get("lang"), words))
    return cues


def merge_and_number(cue_lists):
    """合併多區段 cue（皆已絕對時間）→ 依 start 排序 → 丟空 → 修正 end<=start。"""
    cues = [c for lst in cue_lists for c in lst if (c.get("text") or "").strip()]
    cues.sort(key=lambda c: c["start"])
    for c in cues:
        if c["end"] <= c["start"]:
            c["end"] = c["start"] + 0.5
    return cues


def to_traditional(text):
    """簡體 → 台灣繁體（opencc s2twp）；未裝 opencc 則原樣回傳。僅供中文 cue 使用。"""
    global _cc, _cc_ready
    if not _cc_ready:
        _cc_ready = True
        try:
            from opencc import OpenCC
            for cfg in ("s2twp", "s2tw", "s2t"):
                try:
                    c = OpenCC(cfg); c.convert("测"); _cc = c; break
                except Exception:
                    _cc = None
        except Exception:
            _cc = None
    return _cc.convert(text) if _cc else text


def regroup_sentences(cues, mode="natural", max_chars=38, min_chars=8, max_gap=1.0):
    """用逐字時間把 cue 重切成字幕。
    mode='natural'：視情況一條 1~3 句（過短句合併），保留標點。
    mode='oneline'：一句結束就換一條（每個句末都斷），並去掉標點只留 ！？!?。
    斷句點：句末標點(。！？)且長度>=min_chars、或長度>=max_chars、或時間大跳(>max_gap)。
    標籤 cue([Music]) 與無逐字(words)的 cue 原樣保留為斷句邊界。
    """
    if mode == "oneline":
        min_chars = 1                       # 每個句末都斷，不合併短句
    out = []
    buf = []   # 累積中的 word dict 清單
    buf_lang = None
    fallback_start = 0.0
    fallback_end = 0.0

    def flush():
        if not buf:
            return
        text = "".join(w.get("word", "") for w in buf).strip()
        if text:
            start = next((w["start"] for w in buf if w.get("start") is not None), fallback_start)
            end = next((w["end"] for w in reversed(buf) if w.get("end") is not None), fallback_end)
            out.append(make_cue(start, end, text, buf_lang, deepcopy(buf)))
        buf.clear()

    for c in cues:
        words = c.get("words")
        if c.get("is_tag") or not words:
            flush()
            out.append(deepcopy(c))
            continue
        if buf and c.get("lang") != buf_lang:
            flush()
        buf_lang = c.get("lang")
        fallback_start, fallback_end = c["start"], c["end"]
        for w in words:
            if (buf and w.get("start") is not None and buf[-1].get("end") is not None
                    and w["start"] - buf[-1]["end"] > max_gap):
                flush()
            buf.append(w)
            cur = "".join(x.get("word", "") for x in buf).strip()
            if len(cur) >= max_chars:
                flush()
            elif cur and cur[-1] in _SENT_END and len(cur) >= min_chars:
                flush()
    flush()

    if mode == "oneline":                   # 去標點(留 ！？)，並丟掉變空的 cue
        kept = []
        for c in out:
            if c.get("is_tag"):
                kept.append(c)
            else:
                c["text"] = _strip_soft_punct(c["text"])
                if c["text"]:
                    kept.append(c)
        return kept
    return out


def to_srt(cues, show_lang=False):
    """cue 清單 → SRT 字串。標籤 cue（[Music]）整段一條、不加語言前綴。"""
    lines = []
    for i, c in enumerate(cues, 1):
        text = c["text"]
        if show_lang and c.get("lang") and not c.get("is_tag"):
            text = f"[{c['lang']}] {text}"
        lines.append(f"{i}\n{fmt(c['start'])} --> {fmt(c['end'])}\n{text}\n")
    return "\n".join(lines)


def write_outputs(cues, srt_path, show_lang=False):
    """寫 .srt + .segments.json 側車檔；回傳兩者路徑。"""
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(to_srt(cues, show_lang=show_lang))
    seg_path = os.path.splitext(srt_path)[0] + ".segments.json"
    with open(seg_path, "w", encoding="utf-8") as f:
        json.dump(cues, f, ensure_ascii=False, indent=2)
    return srt_path, seg_path
