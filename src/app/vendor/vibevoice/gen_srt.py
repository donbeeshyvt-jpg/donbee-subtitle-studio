#!/usr/bin/env python
"""把 whisperx/hybrid 的逐字對齊結果(segments[].words) 組成字幕行 SRT。
標記類(如 [Music]) 整段一條、不逐字母切；一般語句依字數/停頓切行，用真實逐字時間。"""
import json, sys

def fmt(t):
    if t is None:
        t = 0.0
    h = int(t // 3600); m = int(t % 3600 // 60); s = int(t % 60); ms = int(round((t - int(t)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def is_tag(text):
    t = text.strip()
    return t.startswith("[") and t.endswith("]")

def group(segments, max_chars=20, max_gap=0.8):
    cues = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        words = seg.get("words") or []
        if is_tag(text) or not words:
            cues.append((seg.get("start"), seg.get("end"), text))
            continue
        line, lstart, lend = [], None, None
        for w in words:
            ws, we, wd = w.get("start"), w.get("end"), w.get("word", "")
            gap = (ws is not None and lend is not None and ws - lend > max_gap)
            cur_len = len("".join(x.get("word", "") for x in line))
            if line and (gap or cur_len >= max_chars):
                cues.append((lstart, lend, "".join(x.get("word", "") for x in line).strip()))
                line, lstart = [], None
            if not line:
                lstart = ws if ws is not None else lend
            line.append(w)
            if we is not None:
                lend = we
        if line:
            cues.append((lstart, lend, "".join(x.get("word", "") for x in line).strip()))
    return cues

def main():
    src, dst = sys.argv[1], sys.argv[2]
    segs = json.load(open(src, encoding="utf-8")).get("segments", [])
    cues = group(segs)
    out, n = [], 0
    for s, e, t in cues:
        if not t:
            continue
        if e is None or (s is not None and e <= s):
            e = (s or 0) + 1.0
        n += 1
        out.append(f"{n}\n{fmt(s)} --> {fmt(e)}\n{t}\n")
    open(dst, "w", encoding="utf-8").write("\n".join(out))
    print(f"wrote {dst}  ({n} cues)")

if __name__ == "__main__":
    main()
