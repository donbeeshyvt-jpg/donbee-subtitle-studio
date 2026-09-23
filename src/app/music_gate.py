"""唱歌/音樂 vs 語音切分閘。

設計：用 AST (AudioSet) 對滑動窗做音訊分類，把連續窗合併成 speech / music 區間。
語音區間才進 ASR；音樂/唱歌區間由 cli 輸出單一 [唱歌]/[Music] cue（不轉歌詞）。

實作備註：**直接用 AutoFeatureExtractor + AutoModelForAudioClassification，不走 transformers
pipeline** —— 因為 pipeline 的 preprocess 會 import torchcodec，而 torchcodec 的 DLL 在本機
ffmpeg 8.1 + torch 2.11 無法載入（DECISION_LOG）。自己用 librosa 載音訊即可繞過。
迴圈為有限窗數，無無限迴圈風險。
"""
from app.config import ASR_SAMPLE_RATE  # noqa: F401 — 觸發 config 設定 HF 環境變數

_extractor = None
_model = None
_MODEL = "MIT/ast-finetuned-audioset-10-10-0.4593"

# AudioSet 標籤中視為「音樂/唱歌」與「語音」的關鍵字（小寫比對）
_MUSIC_KEYS = ("music", "singing", "song", "instrument", "guitar", "piano",
               "drum", "bass", "violin", "melody", "rapping", "choir", "chant")
_SPEECH_KEYS = ("speech", "narration", "conversation", "monologue", "talk")


def _get_model():
    """延遲載入 AST 模型 + 特徵抽取器（首次下載 ~350MB）。不用 pipeline，避開 torchcodec。"""
    global _extractor, _model
    if _model is None:
        import torch
        from transformers import AutoFeatureExtractor, AutoModelForAudioClassification
        _extractor = AutoFeatureExtractor.from_pretrained(_MODEL)
        _model = AutoModelForAudioClassification.from_pretrained(_MODEL)
        _model.to("cuda" if torch.cuda.is_available() else "cpu").eval()
    return _extractor, _model


def _classify(seg, sr):
    """回傳此窗 top-k [{label, score}]。"""
    import torch
    extractor, model = _get_model()
    inputs = extractor(seg, sampling_rate=sr, return_tensors="pt")
    dev = next(model.parameters()).device
    inputs = {k: v.to(dev) for k, v in inputs.items()}
    with torch.no_grad():
        probs = torch.softmax(model(**inputs).logits[0], dim=-1)
    topk = torch.topk(probs, 25)
    id2label = model.config.id2label
    return [{"label": id2label[int(i)], "score": float(p)}
            for p, i in zip(topk.values, topk.indices)]


def _window_label(scores):
    """由 top-k 分數判斷此窗為 music 或 speech。"""
    music = sum(s["score"] for s in scores
                if any(k in s["label"].lower() for k in _MUSIC_KEYS))
    speech = sum(s["score"] for s in scores
                 if any(k in s["label"].lower() for k in _SPEECH_KEYS))
    return "music" if music >= speech else "speech"


def segment(wav_path, win=1.5, min_music=2.0, min_speech=0.6):
    """
    回傳 [(label, start, end)]（label ∈ {speech, music}，本地秒）。
    win：分類窗長。min_music/min_speech：過短段併入鄰段（去抖動）。
    """
    import numpy as np
    import librosa

    y, sr = librosa.load(wav_path, sr=ASR_SAMPLE_RATE, mono=True)
    total = len(y) / sr

    n = max(1, int(np.ceil(total / win)))
    raw = []
    for i in range(n):                        # 有限窗數
        a = i * win
        b = min(total, a + win)
        seg = y[int(a * sr):int(b * sr)]
        if len(seg) < int(0.2 * sr):
            continue
        raw.append((_window_label(_classify(seg, sr)), a, b))

    spans = []                                # 合併連續同標籤
    for lab, a, b in raw:
        if spans and spans[-1][0] == lab:
            spans[-1][2] = b
        else:
            spans.append([lab, a, b])

    smoothed = []                             # 平滑：過短段併入前段
    for lab, a, b in spans:
        dur = b - a
        too_short = (lab == "music" and dur < min_music) or (lab == "speech" and dur < min_speech)
        if smoothed and too_short:
            smoothed[-1][2] = b
        else:
            smoothed.append([lab, a, b])

    return [(lab, a, b) for lab, a, b in smoothed]
