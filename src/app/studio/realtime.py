"""M7 即時字幕與翻譯（2026-09-20 使用者：「M7 也可以納入，然後交叉測試」）。

這台 Windows 沒有 vLLM，串流專用模型（Qwen3-ASR 串流、Voxtral Realtime、VibeVoice-ASR-Streaming）不能在本機跑，
所以用已安裝的 faster-whisper 做「滑動視窗重轉＋前後兩次結果一致的字才定稿」（LocalAgreement-2，whisper_streaming 的做法）：
- 每收到 step_sec 秒新音訊，就把緩衝區（上一行字幕結束之後的音訊）整段再轉一次；
- 這次與上次開頭一致的字才定稿（剛說完、還在尾端的字常聽錯，要等下一次確認），其餘當「暫定」顯示；
- 定稿的字遇到句末標點、字數到上限、或停頓 ≥ pause_sec 就成為一行字幕；行之前的音訊丟掉，每一步只重轉尾端。
翻譯：每一行定稿後交給文字模型（providers.translate，本機 LM Studio 或 OpenRouter 等），結果以事件回傳。
"""
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import numpy as np

SAMPLE_RATE = 16000
MAX_CHUNK_SEC = 5.0
SENTENCE_END = re.compile(r'[。！？!?…]$|(?<![0-9])\.$')
PUNCTUATION = '，,、。．.！!？?；;：:…~〜～・「」『』（）()[]【】"\'-—'


def _bare(text):
    return ''.join(ch for ch in text if not ch.isspace() and ch not in PUNCTUATION)


def _join(words):
    return ''.join(w['text'] for w in words).strip()


def normalizer(language):
    """顯示用文字：中文轉繁體（與批次流程的 asr._normalize 一致）；自動偵測時逐行判斷語言；日文、英文不動。"""
    from .asr import _detect_language, _normalize
    if language == 'zh':
        return lambda text: _normalize(text, 'zh')
    if language == 'auto':
        return lambda text: _normalize(text, 'zh') if _detect_language(text) == 'zh' else text
    return lambda text: text


class StreamingTranscriber:
    """transcribe(samples, *, offset, prompt) → [{'text','start','end'}]（秒，相對 samples 開頭）。"""

    # M7-5（2026-09-21）：視窗 10→6 秒、強制定稿尾端 3→2 秒 —— 一直不一致的段落最慢 6 秒內定稿（原本 p95 7.28、最長 10.2 秒）
    def __init__(self, transcribe, *, step_sec=1.0, max_window_sec=6.0, max_line_chars=18, pause_sec=.8, force_tail_sec=2.0,
                 clock=time.monotonic, normalize=None):
        self.transcribe = transcribe
        self.normalize = normalize or (lambda text: text)
        self.step_sec = step_sec
        # 緩衝區上限：超過就把「force_tail_sec 秒之前」的暫定字強制定稿（2026-09-20 交叉測試：沒有上限時一直不一致的段落
        # 讓緩衝區無限變長，每一步越轉越慢，處理時間為音訊 2 倍、定稿延遲中位 22 秒）
        self.max_window_sec = max_window_sec
        self.force_tail_sec = force_tail_sec
        self.max_line_chars = max_line_chars
        self.pause_sec = pause_sec
        self.clock = clock
        self.buffer = np.zeros(0, dtype=np.float32)
        self.buffer_start = 0.0      # 緩衝區第一個樣本的絕對秒數
        self.received = 0.0          # 已收到的音訊秒數
        self.last_step_at = 0.0
        self.committed_end = 0.0     # 最後一個定稿字的結束秒數
        self.pending = []            # 已定稿、還沒成行的字
        self.previous = []           # 上一次的暫定字（比對用）
        self.lines = []
        self._steps, self._compute, self._max_step, self._lags = 0, 0.0, 0.0, []

    def feed(self, samples):
        samples = np.asarray(samples, dtype=np.float32)
        self.buffer = np.concatenate([self.buffer, samples])
        self.received += len(samples) / SAMPLE_RATE
        if self.received - self.last_step_at + 1e-9 >= self.step_sec:
            return self._step(final=False)
        return []

    def finish(self):
        return self._step(final=True)

    def _prompt(self):
        return ''.join(line['text'] for line in self.lines)[-60:]

    def _step(self, final):
        self.last_step_at = self.received
        started = self.clock()
        words = self.transcribe(self.buffer, offset=self.buffer_start, prompt=self._prompt()) if len(self.buffer) else []
        spent = self.clock() - started
        self._steps += 1
        self._compute += spent
        self._max_step = max(self._max_step, spent)
        hypothesis = []
        for word in words:
            start, end = word['start'] + self.buffer_start, word['end'] + self.buffer_start
            # 已定稿的字（緩衝區裡可能還留著它們的音訊）不再算
            if word['text'].strip() and (start + end) / 2 > self.committed_end - 1e-6:
                hypothesis.append({'text': word['text'], 'start': round(start, 3), 'end': round(end, 3)})
        if final:
            agreed = hypothesis
        else:
            agreed = []
            for new, old in zip(hypothesis, self.previous):
                if _bare(new['text']) != _bare(old['text']) or not _bare(new['text']):
                    break
                agreed.append(new)
        rest = hypothesis[len(agreed):]
        overdue = not final and self.received - self.buffer_start > self.max_window_sec
        if overdue:
            horizon = self.received - self.force_tail_sec
            forced = []
            for word in rest:
                if word['end'] > horizon:
                    break
                forced.append(word)
            agreed, rest = agreed + forced, rest[len(forced):]
        for word in agreed:
            self._lags.append(round(self.received - word['end'], 3))  # 說完到定稿隔了多少秒音訊
        if agreed:
            self.pending += agreed
            self.committed_end = agreed[-1]['end']
        self.previous = rest
        events = self._cut(final or overdue)
        if self.previous and not final:
            events.append({'type': 'tentative', 'text': self.normalize(_join(self.previous)), 'start': self.previous[0]['start']})
        self._trim(overdue)
        return events

    def _cut(self, final):
        events, line = [], []
        for index, word in enumerate(self.pending):
            line.append(word)
            following = self.pending[index + 1] if index + 1 < len(self.pending) else None
            if (SENTENCE_END.search(word['text'].strip()) or len(_bare(_join(line))) >= self.max_line_chars
                    or (following and following['start'] - word['end'] >= self.pause_sec)):
                events.append(self._emit(line))
                line = []
        # 最後一段：結束、停頓夠久、或視窗太長時也成行
        silent = line and not self.previous and self.received - line[-1]['end'] >= self.pause_sec
        if line and (final or silent):
            events.append(self._emit(line))
            line = []
        self.pending = line
        return events

    def _emit(self, words):
        line = {'type': 'line', 'id': f'L{len(self.lines) + 1}', 'start': words[0]['start'], 'end': words[-1]['end'],
                'text': self.normalize(_join(words))}
        self.lines.append({k: v for k, v in line.items() if k != 'type'})
        return line

    def _trim(self, overdue=False):
        # 丟掉最後一行字幕之前的音訊（還沒成行的定稿字保留，讓下一次重轉有前文）
        keep_from = self.pending[0]['start'] if self.pending else (max(self.lines[-1]['end'], self.buffer_start) if self.lines else self.buffer_start)
        if overdue:
            # 超過視窗：最多只留 force_tail_sec 秒前之後的音訊（或第一個暫定字起），沒有字的舊音訊（音樂、雜音）也丟掉
            horizon = self.received - self.force_tail_sec
            keep_from = max(keep_from, min(horizon, self.previous[0]['start'] if self.previous else horizon))
        cut = int(round((keep_from - self.buffer_start) * SAMPLE_RATE))
        if cut > 0:
            self.buffer = self.buffer[cut:]
            self.buffer_start = round(self.buffer_start + cut / SAMPLE_RATE, 6)

    def stats(self):
        lags = sorted(self._lags)
        return {'steps': self._steps, 'audio_sec': round(self.received, 3), 'compute_sec': round(self._compute, 3),
                'real_time_factor': round(self._compute / self.received, 3) if self.received else None,
                'max_step_sec': round(self._max_step, 3), 'lines': len(self.lines),
                'commit_lag_sec': {'median': lags[len(lags) // 2] if lags else None, 'p95': lags[int(len(lags) * .95)] if lags else None,
                                   'max': lags[-1] if lags else None}}


def lines_to_srt(lines):
    from . import subtitles
    cues = [{'id': line['id'], 'start_us': int(round(line['start'] * 1e6)), 'end_us': max(int(round(line['end'] * 1e6)), int(round(line['start'] * 1e6)) + 1000),
             'text': line['text'], 'sequence_item_id': 'live'} for line in lines]
    return subtitles.to_srt(subtitles.ensure_min_duration(cues))


def load_transcriber(model, device, language, hints):
    """即時用的 faster-whisper：整個工作階段載入一次；每一步只轉緩衝區（通常幾秒），不開 VAD。"""
    from faster_whisper import WhisperModel
    from .asr import _device
    chosen = _device(device)
    engine = WhisperModel(model, device=chosen, compute_type='int8_float16' if chosen == 'cuda' else 'int8')

    def transcribe(samples, *, offset=0.0, prompt=''):
        seconds = len(samples) / SAMPLE_RATE
        # 即時只解碼一次（不做溫度重試），輸出長度依視窗長度設上限：音樂／重複段不會拖垮一步
        # （2026-09-20 交叉測試：沒限制時 large-v3 處理時間為音訊 2.8 倍、單步最長 13.6 秒）
        options = dict(word_timestamps=True, vad_filter=False, beam_size=1, condition_on_previous_text=False,
                       without_timestamps=False, initial_prompt=prompt or None, temperature=0.0,
                       max_new_tokens=max(8, min(440, int(15 * seconds) + 10)))
        if language in ('zh', 'ja', 'en'):
            options['language'] = language
        if hints:
            options['hotwords'] = hints
        segments, _info = engine.transcribe(samples, **options)
        rows = []
        for segment in segments:
            for word in segment.words or []:
                rows.append({'text': word.word, 'start': float(word.start), 'end': float(word.end)})
        return rows
    transcribe.engine = engine
    return transcribe


class Sessions:
    """即時工作階段（一次一個：顯示卡）。音訊以 PCM16 單聲道 16 kHz 分段送進來；閒置 idle_sec 秒自動關閉。"""

    def __init__(self, idle_sec=600):
        self.idle_sec = idle_sec
        self.items = {}
        self.lock = threading.Lock()

    def active(self):
        now = time.monotonic()
        with self.lock:
            for sid in [sid for sid, s in self.items.items() if now - s['touched'] > self.idle_sec]:
                self._close(sid)
            return list(self.items)

    def create(self, *, model, device, language, hints, translate_to, provider, secret, step_sec):
        transcribe = load_transcriber(model, device, language, hints)
        sid = 'rt_' + uuid.uuid4().hex[:12]
        session = {'id': sid, 'model': model, 'language': language, 'translate_to': translate_to, 'provider': provider, 'secret': secret,
                   'engine': StreamingTranscriber(transcribe, step_sec=step_sec, normalize=normalizer(language)), 'touched': time.monotonic(), 'events': [],
                   'translations': {}, 'translation_sec': [], 'pool': ThreadPoolExecutor(max_workers=1) if translate_to else None,
                   'futures': [], 'transcribe': transcribe}
        with self.lock:
            self.items[sid] = session
        return session

    def get(self, sid):
        with self.lock:
            session = self.items.get(sid)
        if session is None:
            from .store import StudioError
            raise StudioError('REALTIME_SESSION_NOT_FOUND', '找不到即時字幕工作階段（可能已結束或閒置逾時）', 404)
        session['touched'] = time.monotonic()
        return session

    def _translate(self, session, lines):
        from . import providers
        started = time.monotonic()
        try:
            result = providers.translate([{'id': line['id'], 'text': line['text']} for line in lines], session['provider'],
                                         session['translate_to'], secret=session['secret'])
            elapsed = round(time.monotonic() - started, 3)
            session['translation_sec'].append(elapsed)
            for line in lines:
                if line['id'] in result['translations']:
                    session['translations'][line['id']] = result['translations'][line['id']]
                    session['events'].append({'type': 'translation', 'line_id': line['id'], 'text': result['translations'][line['id']],
                                              'latency_sec': elapsed})
        except providers.ProviderError as error:
            session['events'].append({'type': 'translation_error', 'line_ids': [line['id'] for line in lines], 'code': str(error)})

    def _collect(self, session, events):
        lines = [e for e in events if e['type'] == 'line']
        if lines and session['pool']:
            session['futures'].append(session['pool'].submit(self._translate, session, lines))
        ready, session['events'] = session['events'], []
        return events + ready

    def feed(self, sid, data):
        session = self.get(sid)
        samples = np.frombuffer(data, dtype='<i2').astype(np.float32) / 32768.0
        return self._collect(session, session['engine'].feed(samples))

    def finish(self, sid, wait_sec=60):
        session = self.get(sid)
        events = session['engine'].finish()
        events = self._collect(session, events)
        deadline = time.monotonic() + wait_sec
        for future in session['futures']:
            try:
                future.result(timeout=max(0, deadline - time.monotonic()))
            except Exception:
                pass  # 逾時或失敗的翻譯只是少了譯文，字幕照樣輸出
        ready, session['events'] = session['events'], []
        engine = session['engine']
        lines = [{**line, **({'translation': session['translations'][line['id']]} if line['id'] in session['translations'] else {})}
                 for line in engine.lines]
        translation = sorted(session['translation_sec'])
        result = {'events': events + ready, 'lines': lines, 'srt': lines_to_srt(engine.lines),
                  'stats': {**engine.stats(), 'translation_sec': {'median': translation[len(translation) // 2] if translation else None,
                                                                   'max': translation[-1] if translation else None, 'calls': len(translation)}}}
        with self.lock:
            self._close(sid)
        return result

    def close(self, sid):
        with self.lock:
            self._close(sid)

    def _close(self, sid):
        session = self.items.pop(sid, None)
        if session and session['pool']:
            session['pool'].shutdown(wait=False, cancel_futures=True)
        if session:
            session['transcribe'] = None
            session['engine'].transcribe = None
