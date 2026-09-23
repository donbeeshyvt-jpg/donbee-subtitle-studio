"""AST 多標籤音訊分類；僅產生候選資料，不刪除音訊、不修改原稿或決定 ASR 工作。

閾值尚未校準，尤其不能把「music」視為證明沒有語音。人工覆寫建立衍生區段，
原始窗的分數與分類始終保留。模型僅從本機快取載入，無隱含下載或 GPU 選擇。
"""
from bisect import bisect_right
from copy import deepcopy
import heapq
from functools import lru_cache
import importlib.metadata
import math
from pathlib import Path
import time

MODEL_ID = 'MIT/ast-finetuned-audioset-10-10-0.4593'
SAMPLE_RATE = 16000
LABELS = frozenset({'speech', 'music', 'mixed', 'uncertain'})
MAX_US = 9007199254740991
MAX_INTERLEAVED_SAMPLES = 128_000_000  # 解碼 float32 最多約 512 MB，再加單聲道與重採樣暫存。
THRESHOLDS = {
    'speech_min': .4,
    'speech_protect': .1,
    'music_min': .85,
    'music_without_speech_max': .08,
    'mixed_music_min': .35,
    'digital_silence_rms': .00001,
}
_SPEECH_KEYS = ('speech', 'narration', 'conversation', 'monologue', 'talk', 'whispering')
_MUSIC_KEYS = ('music', 'singing', 'song', 'instrument', 'guitar', 'piano', 'drum',
               'bass', 'violin', 'melody', 'rapping', 'choir', 'chant')


def _version(package):
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def _decode_audio(path):
    """讀取一次 PCM，單聲道化及必要重採樣均在記憶體內完成，沒有逐窗 FFmpeg。"""
    import numpy as np
    import soundfile as sf
    path = Path(path)
    if path.suffix.lower() != '.wav':
        raise ValueError('EXPECTED_WAV: 請提供已取得的 WAV 音訊')
    with sf.SoundFile(path) as stream:
        sample_rate = stream.samplerate
        if stream.frames <= 0:
            raise ValueError('EMPTY_AUDIO')
        if not 8000 <= sample_rate <= 192000 or not 1 <= stream.channels <= 8:
            raise ValueError('UNSUPPORTED_AUDIO_FORMAT')
        if stream.frames * stream.channels > MAX_INTERLEAVED_SAMPLES:
            raise ValueError('AUDIO_TOO_LARGE: 請使用已取得的較短音訊區段')
        samples = stream.read(dtype='float32', always_2d=True)
    if not np.isfinite(samples).all():
        raise ValueError('NONFINITE_AUDIO')
    mono = samples.mean(axis=1, dtype=np.float32)
    del samples
    if sample_rate != SAMPLE_RATE:
        from scipy.signal import resample_poly
        divisor = math.gcd(sample_rate, SAMPLE_RATE)
        mono = resample_poly(mono, SAMPLE_RATE // divisor, sample_rate // divisor).astype(np.float32)
    if not len(mono):
        raise ValueError('EMPTY_AUDIO')
    return mono, SAMPLE_RATE


@lru_cache(maxsize=2)
def _load_model(device):
    """快取只存在目前工作程序，分 CPU／CUDA；程序結束即釋放，不改共享模型檔案。"""
    from transformers import AutoFeatureExtractor, AutoModelForAudioClassification
    extractor = AutoFeatureExtractor.from_pretrained(MODEL_ID, local_files_only=True, trust_remote_code=False)
    model = AutoModelForAudioClassification.from_pretrained(MODEL_ID, local_files_only=True, trust_remote_code=False)
    model.to(device).eval()
    labels = {int(key): str(value) for key, value in model.config.id2label.items()}
    metadata = {'model_id': MODEL_ID, 'revision': getattr(model.config, '_commit_hash', None),
                'torch': _version('torch'), 'transformers': _version('transformers')}
    return extractor, model, labels, metadata


def _predict(extractor, model, windows, sample_rate, device):
    import torch
    # AudioSet 是多標籤問題；speech/music 可同時高分，不能使用互斥 softmax。
    inputs = extractor(list(windows), sampling_rate=sample_rate, return_tensors='pt')
    inputs = {key: value.to(device) for key, value in inputs.items()}
    with torch.inference_mode():
        logits = model(**inputs).logits
        return torch.sigmoid(logits).float().cpu().numpy()


def classify_probabilities(probabilities, id2label):
    """純函式：同一家族使用最大值，避免 AudioSet 父子標籤重複加總成假高信心。"""
    values = list(probabilities)
    if not values or len(values) != len(id2label):
        raise ValueError('INVALID_CLASSIFIER_OUTPUT')
    if any(isinstance(x, bool) or not math.isfinite(float(x)) or not 0 <= float(x) <= 1 for x in values):
        raise ValueError('INVALID_CLASSIFIER_PROBABILITY')
    labels = {int(key): str(value).lower() for key, value in id2label.items()}
    if set(labels) != set(range(len(values))):
        raise ValueError('INVALID_LABEL_MAP')
    speech_indices = [i for i, label in labels.items() if any(k in label for k in _SPEECH_KEYS)]
    music_indices = [i for i, label in labels.items() if any(k in label for k in _MUSIC_KEYS)]
    if not speech_indices or not music_indices:
        raise ValueError('UNSUPPORTED_LABEL_MAP')
    speech = max(float(values[i]) for i in speech_indices)
    music = max(float(values[i]) for i in music_indices)
    if speech >= THRESHOLDS['speech_protect'] and music >= THRESHOLDS['mixed_music_min']:
        label, reason = 'mixed', 'speech_present_with_music'
    elif speech >= THRESHOLDS['speech_min']:
        label, reason = 'speech', 'speech_evidence'
    elif music >= THRESHOLDS['music_min'] and speech < THRESHOLDS['music_without_speech_max']:
        label, reason = 'music', 'strong_music_low_speech_evidence'
    else:
        label, reason = 'uncertain', 'insufficient_or_conflicting_evidence'
    return {'label': label, 'speech_score': speech, 'music_score': music, 'reason': reason,
            'requires_review': label in {'mixed', 'uncertain'}}


def classify_audio(wav_path, batch_size=2, window_sec=10.0, device='cpu', progress=None):
    """分類本機 WAV，回傳 raw_spans／spans／settings；時間是該 WAV 的本地微秒。

    progress 若提供，以關鍵字接收 completed_windows/total_windows/model_batches。
    單一模型 forward 不能在此函式內安全中斷，呼叫端須使用既有 supervisor 的程序期限。
    零值窗保留 uncertain，不硬轉成 music。尾窗保留實際終點，由 AST extractor 做 padding。
    """
    import numpy as np
    if type(batch_size) is not int or not 1 <= batch_size <= 64:
        raise ValueError('INVALID_BATCH_SIZE')
    if isinstance(window_sec, bool) or not isinstance(window_sec, (int, float)) or not math.isfinite(window_sec) or not .2 <= window_sec <= 10.24:
        raise ValueError('INVALID_WINDOW_SECONDS')
    if device not in {'cpu', 'cuda'}:
        raise ValueError('INVALID_DEVICE: 僅接受明示 cpu 或 cuda')
    if progress is not None and not callable(progress):
        raise ValueError('INVALID_PROGRESS_CALLBACK')
    start = time.monotonic()
    audio, sample_rate = _decode_audio(wav_path)
    # 使用整數 sample 邊界，避免窗長浮點累加及尾段消失。
    window_samples = round(window_sec * sample_rate)
    count = math.ceil(len(audio) / window_samples)
    raw = []
    model_parts = None
    model_batches = 0
    for batch_start in range(0, count, batch_size):
        windows, indices, records = [], [], []
        for index in range(batch_start, min(count, batch_start + batch_size)):
            a, b = index * window_samples, min(len(audio), (index + 1) * window_samples)
            window = audio[a:b]
            rms = float(np.sqrt(np.mean(np.square(window, dtype=np.float64))))
            record = {'id': f'classifier_window_{index:06d}', 'start_us': round(a * 1e6 / sample_rate),
                      'end_us': round(b * 1e6 / sample_rate), 'sample_start': a, 'sample_end': b,
                      'rms': rms, 'label': 'uncertain', 'reason': 'digital_silence',
                      'speech_score': None, 'music_score': None, 'requires_review': True}
            records.append(record)
            if rms > THRESHOLDS['digital_silence_rms']:
                indices.append(len(records) - 1)
                windows.append(window)
        if windows:
            if model_parts is None:
                model_parts = _load_model(device)
            extractor, model, labels, metadata = model_parts
            probabilities = _predict(extractor, model, windows, sample_rate, device)
            if len(probabilities) != len(windows):
                raise ValueError('INVALID_CLASSIFIER_BATCH_LENGTH')
            for index, scores in zip(indices, probabilities):
                records[index].update(classify_probabilities(scores, labels))
            model_batches += 1
        raw.extend(records)
        if progress:
            progress(completed_windows=len(raw), total_windows=count, model_batches=model_batches)
    settings = {'schema_version': 1, 'classifier': 'ast_audioset', 'model_id': MODEL_ID,
                'model_loaded': model_parts is not None, 'device': device, 'sample_rate': sample_rate,
                'window_sec': window_sec, 'window_samples': window_samples, 'batch_size': batch_size,
                'model_batches': model_batches, 'activation': 'sigmoid', 'family_aggregation': 'max',
                'thresholds': dict(THRESHOLDS), 'calibrated': False,
                'warnings': ['分類閾值尚未校準；music 不代表已證明無語音，人工覆寫及原稿必須保留。'],
                'timebase': 'asset', 'elapsed_sec': time.monotonic() - start,
                'numpy': _version('numpy'), 'soundfile': _version('soundfile'),
                'model_metadata': model_parts[3] if model_parts else None}
    return {'raw_spans': raw, 'spans': deepcopy(raw), 'settings': settings}


def _validate_span(span):
    if not isinstance(span, dict) or span.get('label') not in LABELS:
        raise ValueError('INVALID_CLASSIFICATION_LABEL')
    a, b = span.get('start_us'), span.get('end_us')
    if type(a) is not int or type(b) is not int or not 0 <= a < b <= MAX_US:
        raise ValueError('INVALID_CLASSIFICATION_RANGE')
    if not isinstance(span.get('id'), str) or not span['id']:
        raise ValueError('INVALID_CLASSIFICATION_ID')


def apply_overrides(raw_spans, overrides):
    """純衍生操作；重疊人工覆寫依輸入順序後者優先，不能跨越未分類的空白。

    不合併窗以保留其原始分數。輸出保留 raw_span_id/model_label/override_id，
    以不同衍生 ID 表示分割；raw_spans 與 overrides（包含巢狀資料）皆不修改。
    """
    if not isinstance(raw_spans, list) or not isinstance(overrides, list):
        raise ValueError('INVALID_CLASSIFICATION_COLLECTION')
    if len(raw_spans) > 100000 or len(overrides) > 10000:
        raise ValueError('CLASSIFICATION_COLLECTION_TOO_LARGE')
    for span in [*raw_spans, *overrides]:
        _validate_span(span)
    for collection in (raw_spans, overrides):
        if len({x['id'] for x in collection}) != len(collection):
            raise ValueError('DUPLICATE_CLASSIFICATION_ID')
    ordered = sorted(raw_spans, key=lambda x: x['start_us'])
    if any(a['end_us'] > b['start_us'] for a, b in zip(ordered, ordered[1:])):
        raise ValueError('OVERLAPPING_RAW_CLASSIFICATION')
    starts = [span['start_us'] for span in ordered]
    ends = [span['end_us'] for span in ordered]
    prefix = [0]
    for span in ordered:
        prefix.append(prefix[-1] + span['end_us'] - span['start_us'])

    def covered_until(point):
        completed = bisect_right(ends, point)
        partial = max(0, point - starts[completed]) if completed < len(starts) else 0
        return prefix[completed] + partial

    for override in overrides:
        covered = covered_until(override['end_us']) - covered_until(override['start_us'])
        if covered != override['end_us'] - override['start_us']:
            raise ValueError('OVERRIDE_UNCOVERED_RANGE')
    # 全域邊界掃描與 priority heap，避免每個原始窗逐一掃描所有覆寫。
    cuts = sorted({point for span in [*ordered, *overrides] for point in (span['start_us'], span['end_us'])})
    override_order = sorted(enumerate(overrides), key=lambda item: item[1]['start_us'])
    cursor = 0
    active = []
    counters = {}
    result = []
    for a, b in zip(cuts, cuts[1:]):
        while cursor < len(override_order) and override_order[cursor][1]['start_us'] <= a:
            priority, override = override_order[cursor]
            heapq.heappush(active, (-priority, override['end_us']))
            cursor += 1
        while active and active[0][1] <= a:
            heapq.heappop(active)
        raw_index = bisect_right(starts, a) - 1
        if raw_index < 0 or ordered[raw_index]['end_us'] < b:
            continue
        raw = ordered[raw_index]
        index = counters.get(raw_index, 0)
        counters[raw_index] = index + 1
        span = deepcopy(raw)
        span.update(id=f"{raw['id']}:effective:{index}", raw_span_id=raw['id'],
                    model_label=raw['label'], start_us=a, end_us=b)
        for field in ('sample_start', 'sample_end'):
            if field in span:
                span[f'raw_{field}'] = span.pop(field)
        if active:
            chosen = overrides[-active[0][0]]
            span.update(label=chosen['label'], override_id=chosen['id'],
                        override_reason=str(chosen.get('reason', '')), provenance='manual',
                        requires_review=chosen['label'] == 'uncertain')
        result.append(span)
    return result
