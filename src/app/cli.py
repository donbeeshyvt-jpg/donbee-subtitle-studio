"""端到端串接：URL/檔案 + 區段 → 單一 SRT（與媒體同目錄）。

流程：取時長 → 正規化區段 → 切片 → 每區段音樂閘
  - music 段：輸出單一 [唱歌]/[Music] cue（不進 ASR）
  - speech 段：切出子片 → WhisperX 轉錄+對齊 → 逐段語言
→ 加 offset 映回絕對時間 → 合併重編號 → 寫 .srt + .segments.json
"""
import os
import argparse
import subprocess
from app import regions, music_gate, asr_default, asr_hybrid, lid, srt_build
from app.config import ASR_SAMPLE_RATE


def parse_regions(s):
    """'70-130,200-260' → [(70.0,130.0),(200.0,260.0)]；空字串 → []（整檔）。"""
    if not s:
        return []
    out = []
    for part in s.split(","):
        part = part.strip()
        if part:
            a, b = part.split("-")
            out.append((float(a), float(b)))
    return out


def _subclip(src_wav, a, b, out_wav):
    """從 region wav 取出 [a,b]（本地秒）的子片。"""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{a:.3f}", "-i", src_wav,
         "-t", f"{b - a:.3f}", "-ac", "1", "-ar", str(ASR_SAMPLE_RATE),
         "-c:a", "pcm_s16le", out_wav], check=True)
    return out_wav


def transcribe_media(media_path, region_list, work_dir, out_srt=None, show_lang=False,
                     engine="whisperx", model="large-v3", srt_mode="natural"):
    """核心管線。engine: whisperx/hybrid；model 僅 whisperx 用；srt_mode: natural/oneline。"""
    os.makedirs(work_dir, exist_ok=True)
    dur = regions.ffprobe_duration(media_path)
    spans = regions.normalize(region_list, dur)
    slices = regions.slice_audio(media_path, spans, work_dir)

    all_cues = []
    for wav, offset in slices:
        for label, a, b in music_gate.segment(wav):
            abs_a, abs_b = a + offset, b + offset
            if label == "music":
                # 標記 cue；語言/[唱歌]文字在最後依鄰近語言決定
                all_cues.append([srt_build.make_cue(abs_a, abs_b, "[Music]")])
            else:
                sub = _subclip(wav, a, b, os.path.join(work_dir, f"sp_{int(abs_a * 1000)}.wav"))
                res = (asr_hybrid.transcribe_align(sub) if engine == "hybrid"
                       else asr_default.transcribe_align(sub, model_name=model))
                all_cues.append(srt_build.offset_segments(res["segments"], abs_a))

    merged = srt_build.merge_and_number(all_cues)
    lines = srt_build.regroup_sentences(merged, mode=srt_mode)   # natural 或 oneline

    # 逐句判語言；中文轉台灣繁體；唱歌標記依前一句語言
    prev = None
    for c in lines:
        if c.get("is_tag"):
            c["text"] = "[唱歌]" if prev == "zh" else "[Music]"
            continue
        c["lang"] = lid.label_cue(c["text"], prev)
        if c["lang"]:
            prev = c["lang"]
        if c["lang"] == "zh":
            c["text"] = srt_build.to_traditional(c["text"])

    if out_srt is None:
        out_srt = os.path.splitext(media_path)[0] + ".srt"
    return srt_build.write_outputs(lines, out_srt, show_lang=show_lang)


def main():
    ap = argparse.ArgumentParser(description="字幕工作室 MVP 無頭管線")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", help="YouTube 網址")
    g.add_argument("--file", help="本機影片/音訊檔")
    ap.add_argument("--regions", default="", help="區段，如 70-130,200-260；空＝整檔")
    ap.add_argument("--out", default=None, help="輸出 .srt 路徑（預設與媒體同目錄）")
    ap.add_argument("--work", default=None, help="暫存切片資料夾")
    ap.add_argument("--show-lang", action="store_true", help="字幕前綴語言碼 [ja]/[zh]")
    ap.add_argument("--engine", default="whisperx", choices=["whisperx", "hybrid"],
                    help="whisperx(快、預設) 或 hybrid(VibeVoice 最佳文字，較慢)")
    ap.add_argument("--model", default="large-v3",
                    help="whisperx 模型：large-v3(準) / large-v3-turbo(快)")
    ap.add_argument("--srt-mode", default="natural", choices=["natural", "oneline"],
                    help="natural(自然分句 2~3句) 或 oneline(一句一行，去標點只留 !?)")
    args = ap.parse_args()

    if args.url:
        from app import download
        info = download.fetch(args.url, args.work or os.getcwd())
        media = info["path"]
    else:
        media = args.file
    work = args.work or (os.path.splitext(media)[0] + "_work")
    srt, seg = transcribe_media(media, parse_regions(args.regions), work,
                                out_srt=args.out, show_lang=args.show_lang,
                                engine=args.engine, model=args.model, srt_mode=args.srt_mode)
    print(f"OK -> {srt}")
    print(f"     {seg}")


if __name__ == "__main__":
    main()
