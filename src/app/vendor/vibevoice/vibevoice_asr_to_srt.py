#!/usr/bin/env python
"""
VibeVoice-ASR -> SRT 字幕輸出工具
=================================

把一個音檔（或已抽出音軌的影片）丟給 VibeVoice-ASR，
直接輸出 .srt 字幕檔，方便拿去剪輯軟體用。

特色：
  - 單人 / 多人都能跑（單人預設不標講者）
  - --hotwords 餵專有名詞 / 人名 / 術語，提升辨識
  - --traditional 用 opencc 把簡體轉成台灣繁體（需 pip install opencc-python-reimplemented）
  - --max-chars 把過長的句子自動切成適合閱讀的字幕行（時間軸按字數比例分配）
  - 同時輸出 .raw.txt 和 .segments.json，方便你比對 / 除錯

用法（Windows PowerShell，先 cd 到 VibeVoice 專案資料夾，並已 pip install -e .）：

  python vibevoice_asr_to_srt.py --audio "C:\\path\\to\\audio.wav"

  # 餵專有名詞 + 轉繁體 + 過長句切到每行 ~40 字
  python vibevoice_asr_to_srt.py --audio in.wav --traditional --max-chars 40 ^
      --hotwords "陽明交通大學, 黃仁勳, CUDA"

影片(mp4)請先抽音軌（需 ffmpeg）：
  ffmpeg -i in.mp4 -ar 24000 -ac 1 -vn out.wav
"""

import os
import sys
import json
import re
import argparse
import time

import torch

from vibevoice.modular.modeling_vibevoice_asr import VibeVoiceASRForConditionalGeneration
from vibevoice.processor.vibevoice_asr_processor import VibeVoiceASRProcessor


# --------------------------------------------------------------------------- #
# 時間 / 文字工具
# --------------------------------------------------------------------------- #
def to_seconds(val):
    """把模型輸出的時間（float 秒，或 'HH:MM:SS' / 'MM:SS' 字串）統一轉成 float 秒。"""
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        v = val.strip()
        if not v:
            return None
        if ":" in v:
            try:
                parts = [float(p) for p in v.split(":")]
            except ValueError:
                return None
            sec = 0.0
            for p in parts:
                sec = sec * 60.0 + p
            return sec
        try:
            return float(v)
        except ValueError:
            return None
    return None


def srt_timestamp(seconds: float) -> str:
    """float 秒 -> SRT 時間碼 HH:MM:SS,mmm"""
    if seconds is None or seconds < 0:
        seconds = 0.0
    ms_total = int(round(seconds * 1000))
    h, ms_total = divmod(ms_total, 3_600_000)
    m, ms_total = divmod(ms_total, 60_000)
    s, ms = divmod(ms_total, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _wrap_long(chunk: str, max_chars: int):
    """單一過長片段折行：有空白(英文)斷在詞邊界；無空白(中日韓)先在次級標點、再硬切。"""
    chunk = chunk.strip()
    if not chunk:
        return []
    if len(chunk) <= max_chars:
        return [chunk]
    out = []
    if " " in chunk:                       # 英文等：用詞邊界，不切斷單字
        cur = ""
        for w in chunk.split():
            if cur and len(cur) + 1 + len(w) > max_chars:
                out.append(cur)
                cur = ""
            cur = (cur + " " + w).strip() if cur else w
            while len(cur) > max_chars:    # 單字本身超長才硬切
                out.append(cur[:max_chars])
                cur = cur[max_chars:]
        if cur:
            out.append(cur)
    else:                                  # 中日韓：先在逗號/頓號等斷，再硬切
        cur = ""
        for p in re.split(r'(?<=[，、：；,])', chunk):
            if cur and len(cur) + len(p) > max_chars:
                out.append(cur)
                cur = ""
            cur += p
            while len(cur) > max_chars:
                out.append(cur[:max_chars])
                cur = cur[max_chars:]
        if cur:
            out.append(cur)
    return [o.strip() for o in out if o.strip()]


def split_text(text: str, max_chars: int):
    """切成 <= max_chars 的字幕行：先用句末標點斷句，過長句再折行（英文不切斷單字）。"""
    text = (text or "").strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return [text] if text else []

    sentences = re.split(r'(?<=[。！？!?；;\n])', text)
    pieces, cur = [], ""
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if cur and len(cur) + 1 + len(s) > max_chars:
            pieces.append(cur)
            cur = ""
        if len(s) <= max_chars:
            cur = (cur + " " + s).strip() if cur else s
        else:
            if cur:
                pieces.append(cur)
                cur = ""
            wrapped = _wrap_long(s, max_chars)
            pieces.extend(wrapped[:-1])
            cur = wrapped[-1] if wrapped else ""
    if cur:
        pieces.append(cur)
    return [p.strip() for p in pieces if p.strip()]


def make_converter(to_traditional: bool):
    """建立 opencc 簡->繁(台灣) 轉換器；沒裝就回 None 並提示。"""
    if not to_traditional:
        return None
    try:
        from opencc import OpenCC
    except Exception:
        print("[警告] 未安裝 opencc，跳過繁體轉換。"
              "請執行: pip install opencc-python-reimplemented", file=sys.stderr)
        return None
    for cfg in ("s2twp", "s2tw", "s2t"):
        try:
            cc = OpenCC(cfg)
            cc.convert("测试")  # 確認可用
            return cc
        except Exception:
            continue
    print("[警告] opencc 設定載入失敗，跳過繁體轉換。", file=sys.stderr)
    return None


# --------------------------------------------------------------------------- #
# segments -> SRT
# --------------------------------------------------------------------------- #
def segments_to_srt(segments, max_chars=0, converter=None, with_speaker=False):
    blocks = []
    idx = 1
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue

        start = to_seconds(seg.get("start_time"))
        end = to_seconds(seg.get("end_time"))
        if start is None:
            start = 0.0
        if end is None or end <= start:
            # 沒有可用結束時間時，用字數粗估一個長度（避免 0 長度字幕）
            end = start + max(1.0, len(text) * 0.18)

        if converter is not None:
            text = converter.convert(text)

        speaker = seg.get("speaker_id")
        prefix = ""
        if with_speaker and speaker not in (None, "", "N/A"):
            prefix = f"[S{speaker}] "

        pieces = split_text(text, max_chars)
        total_chars = sum(len(p) for p in pieces) or 1
        span = end - start
        cursor = start
        for p in pieces:
            piece_end = cursor + span * (len(p) / total_chars)
            blocks.append(
                f"{idx}\n{srt_timestamp(cursor)} --> {srt_timestamp(piece_end)}\n{prefix}{p}\n"
            )
            idx += 1
            cursor = piece_end

    return "\n".join(blocks)


# --------------------------------------------------------------------------- #
# 模型載入 / 推論
# --------------------------------------------------------------------------- #
def pick_attn(device, requested):
    if requested != "auto":
        return requested
    if device == "cuda" and torch.cuda.is_available():
        try:
            import flash_attn  # noqa: F401
            return "flash_attention_2"
        except ImportError:
            print("[資訊] 未安裝 flash_attn，改用 sdpa（Windows 一般情況，正常）。")
            return "sdpa"
    return "sdpa"


def _vram(tag=""):
    if torch.cuda.is_available():
        used = torch.cuda.memory_allocated() / 1e9
        peak = torch.cuda.max_memory_allocated() / 1e9
        print(f"[VRAM] {tag} 目前 {used:.2f} GB / 尖峰 {peak:.2f} GB")


def load_model(model_path, device, attn, quant="none"):
    print(f"[資訊] 載入 VibeVoice-ASR：{model_path}（quant={quant}）")
    processor = VibeVoiceASRProcessor.from_pretrained(
        model_path, language_model_pretrained_name="Qwen/Qwen2.5-7B"
    )

    kwargs = dict(attn_implementation=attn, trust_remote_code=True)

    if quant in ("4bit", "8bit") and device == "cuda":
        # 4-bit / 8-bit 量化：權重大幅縮小，塞進小顯存的關鍵。
        from transformers import BitsAndBytesConfig
        if quant == "4bit":
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
            )
        else:
            kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
        kwargs["device_map"] = {"": 0}          # 全放 GPU0；量化時不可再 .to()
        model = VibeVoiceASRForConditionalGeneration.from_pretrained(model_path, **kwargs)
    else:
        # 不量化：bf16（cuda）或 float32（cpu）；device=auto 時讓 accelerate 自動分配/溢位到 CPU
        kwargs["dtype"] = torch.bfloat16 if device == "cuda" else torch.float32
        if device == "auto":
            kwargs["device_map"] = "auto"
        model = VibeVoiceASRForConditionalGeneration.from_pretrained(model_path, **kwargs)
        if device != "auto":
            model = model.to(device)

    model.eval()
    print(f"[資訊] 模型就緒，device={device}, attn={attn}, quant={quant}")
    _vram("載入後")
    return model, processor


def transcribe(model, processor, audio_path, device,
               hotwords=None, max_new_tokens=32768, temperature=0.0, top_p=1.0):
    inputs = processor(
        audio=audio_path,
        sampling_rate=None,
        return_tensors="pt",
        padding=True,
        add_generation_prompt=True,
        context_info=hotwords,           # hotwords / 背景資訊就走這裡
    )
    inputs = {k: (v.to(device) if isinstance(v, torch.Tensor) else v)
              for k, v in inputs.items()}

    gen = {
        "max_new_tokens": max_new_tokens,
        "pad_token_id": processor.pad_id,
        "eos_token_id": processor.tokenizer.eos_token_id,
        "do_sample": temperature > 0,
    }
    if temperature > 0:
        gen["temperature"] = temperature
        gen["top_p"] = top_p

    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    with torch.no_grad():
        output_ids = model.generate(**inputs, **gen)
    dt = time.time() - t0
    _vram("生成後")

    input_len = inputs["input_ids"].shape[1]
    generated_ids = output_ids[0, input_len:]
    raw_text = processor.decode(generated_ids, skip_special_tokens=True)
    try:
        segments = processor.post_process_transcription(raw_text)
    except Exception as e:
        print(f"[警告] 結構化輸出解析失敗：{e}")
        segments = []
    print(f"[資訊] 生成完成，用時 {dt:.1f}s，解析出 {len(segments)} 段。")
    return raw_text, segments


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="VibeVoice-ASR -> SRT 字幕工具")
    ap.add_argument("--audio", required=True, help="音檔路徑（wav/mp3/flac…；影片請先抽音軌）")
    ap.add_argument("--output", default=None, help="輸出 .srt 路徑（預設與音檔同名）")
    ap.add_argument("--model_path", default="microsoft/VibeVoice-ASR", help="模型路徑或 HF 名稱")
    ap.add_argument("--hotwords", default=None, help="專有名詞/人名/術語，逗號分隔，提升辨識")
    ap.add_argument("--traditional", action="store_true", help="用 opencc 轉成台灣繁體")
    ap.add_argument("--max-chars", type=int, default=0,
                    help="每行字幕最大字數，過長自動切（0=不切，先看模型原始斷句）")
    ap.add_argument("--speaker", action="store_true", help="字幕前面標 [S0]/[S1] 講者")
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu",
                    choices=["cuda", "cpu", "auto"])
    ap.add_argument("--attn", default="auto",
                    choices=["auto", "flash_attention_2", "sdpa", "eager"])
    ap.add_argument("--quant", default="4bit", choices=["none", "8bit", "4bit"],
                    help="量化以塞進小顯存（你的 12GB 建議 4bit；none=原始 bf16 需 16GB+）")
    ap.add_argument("--max_new_tokens", type=int, default=32768)
    ap.add_argument("--temperature", type=float, default=0.0)
    args = ap.parse_args()

    if not os.path.isfile(args.audio):
        print(f"[錯誤] 找不到音檔：{args.audio}", file=sys.stderr)
        sys.exit(1)

    out_srt = args.output or (os.path.splitext(args.audio)[0] + ".srt")
    base = os.path.splitext(out_srt)[0]

    attn = pick_attn(args.device, args.attn)
    model, processor = load_model(args.model_path, args.device, attn, quant=args.quant)

    raw_text, segments = transcribe(
        model, processor, args.audio, args.device,
        hotwords=args.hotwords,
        max_new_tokens=args.max_new_tokens,
        temperature=args.temperature,
    )

    # 除錯/比對用：原始輸出 + 結構化 JSON
    with open(base + ".raw.txt", "w", encoding="utf-8") as f:
        f.write(raw_text)
    with open(base + ".segments.json", "w", encoding="utf-8") as f:
        json.dump(segments, f, ensure_ascii=False, indent=2)

    if not segments:
        print("[警告] 沒有解析出任何字幕段落，請看 .raw.txt 確認模型實際輸出。", file=sys.stderr)
        sys.exit(2)

    converter = make_converter(args.traditional)
    srt = segments_to_srt(
        segments,
        max_chars=args.max_chars,
        converter=converter,
        with_speaker=args.speaker,
    )
    with open(out_srt, "w", encoding="utf-8") as f:
        f.write(srt)

    print(f"\n✅ 完成：{out_srt}")
    print(f"   原始輸出：{base}.raw.txt")
    print(f"   結構化  ：{base}.segments.json")
    # 印前幾段給你快速檢查時間軸
    print("\n--- 前 5 段預覽 ---")
    for seg in segments[:5]:
        print(f"[{seg.get('start_time')} - {seg.get('end_time')}] "
              f"S{seg.get('speaker_id')}: {str(seg.get('text',''))[:50]}")


if __name__ == "__main__":
    main()
