"""M7 即時字幕與翻譯（2026-09-20 使用者：「M7 也可以納入，然後交叉測試」）。
這台 Windows 沒有 vLLM，串流專用模型（Qwen3-ASR 串流、Voxtral Realtime、VibeVoice-ASR-Streaming）無法在本機跑；
先用已安裝的 faster-whisper 做「分段滑動視窗＋兩次一致才定稿（LocalAgreement）」的即時字幕，定稿的每一行交給文字模型翻譯。
模型與 HTTP 供應者一律是替身。"""
import io
import json
import time
import wave

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.studio import providers, realtime
from test_studio_provider_setup import fake_provider

# 假想的說話內容（絕對秒數）：「大家好。」停頓後「今天玩遊戲」
WORDS = [('大', .2, .4), ('家', .4, .6), ('好。', .6, .9), ('今', 1.8, 2.0), ('天', 2.0, 2.2), ('玩', 2.2, 2.4), ('遊', 2.4, 2.6), ('戲', 2.6, 2.9)]
UNSTABLE = {'好。': '號', '戲': '系'}  # 剛說完、還在音訊尾端時第一次常聽錯


def fake_transcribe(samples, *, offset, prompt=''):
    available = offset + len(samples) / 16000
    rows = []
    for text, start, end in WORDS:
        if start < offset - 1e-6 or end > available + 1e-6:
            continue
        shown = UNSTABLE.get(text, text) if end > available - .35 else text
        rows.append({'text': shown, 'start': round(start - offset, 3), 'end': round(end - offset, 3)})
    return rows


def stream(session, seconds=3.5, chunk=.5):
    events = []
    for _ in range(int(seconds / chunk)):
        events += session.feed(np.zeros(int(16000 * chunk), dtype=np.float32))
    return events + session.finish()


def test_only_words_two_passes_agree_on_become_subtitle_lines():
    session = realtime.StreamingTranscriber(fake_transcribe, step_sec=.5)
    events = stream(session)
    lines = [(e['text'], e['start'], e['end']) for e in events if e['type'] == 'line']
    assert lines == [('大家好。', .2, .9), ('今天玩遊戲', 1.8, 2.9)]
    tentative = [e['text'] for e in events if e['type'] == 'tentative']
    assert any('號' in t for t in tentative) and any('系' in t for t in tentative)  # 聽錯的只出現在「暫定」，不進字幕
    stats = session.stats()
    assert stats['steps'] == 8 and stats['commit_lag_sec']['max'] <= 1.2 and stats['audio_sec'] == 3.5
    assert session.buffer_start >= .9  # 定稿的行之前的音訊已丟掉，每一步只重轉尾端


def test_lines_are_also_cut_by_length_and_saved_as_srt():
    session = realtime.StreamingTranscriber(fake_transcribe, step_sec=.5, max_line_chars=3)
    lines = [e['text'] for e in stream(session) if e['type'] == 'line']
    assert lines == ['大家好。', '今天玩', '遊戲']
    srt = realtime.lines_to_srt(session.lines)
    assert srt.startswith('1\n00:00:00,200 --> 00:00:00,900\n大家好。\n')


def test_translation_goes_through_the_text_model_port():
    body = {'choices': [{'message': {'content': json.dumps({'translations': [{'id': 'L1', 'text': 'Hi everyone.'}, {'id': 'L2', 'text': "Let's play."}]})}}]}
    with fake_provider({('POST', '/v1/chat/completions'): (200, body)}) as (url, calls):
        result = providers.translate([{'id': 'L1', 'text': '大家好。'}, {'id': 'L2', 'text': '今天玩遊戲'}],
                                     {'base_url': url, 'model': 'm', 'response_format_mode': 'json_schema', 'max_retries': 0}, 'en')
    assert result['translations'] == {'L1': 'Hi everyone.', 'L2': "Let's play."} and result['model'] == 'm'
    sent = calls[0][2]
    assert sent['response_format']['type'] == 'json_schema' and '大家好。' in sent['messages'][1]['content']
    assert 'English' in sent['messages'][0]['content']


def pcm(seconds):
    return (np.zeros(int(16000 * seconds), dtype=np.int16)).tobytes()


def test_realtime_session_api_streams_lines_and_translations(tmp_path, monkeypatch):
    from app.studio.api import create_app
    monkeypatch.setattr(realtime, 'load_transcriber', lambda model, device, language, hints: fake_transcribe)
    body = {'choices': [{'message': {'content': json.dumps({'translations': [{'id': 'L1', 'text': 'Hi everyone.'}]})}}]}
    with fake_provider({('POST', '/v1/chat/completions'): (200, body)}) as (url, _):
        app = create_app(tmp_path / 'state', start_workers=False)
        app.state.config['providers'].append({'id': 'fake-local', 'adapter': 'openai_compatible', 'base_url': url, 'model': 'm',
                                              'response_format_mode': 'json_schema', 'max_retries': 0})
        with TestClient(app) as c:
            c.get('/v1/session')
            assert c.post('/v1/realtime/sessions', json={'translate_to': 'en', 'provider_id': 'api-openrouter'}).json()['error']['code'] == 'REMOTE_CONSENT_REQUIRED'
            assert c.post('/v1/realtime/sessions', json={'translate_to': 'en'}).json()['error']['code'] == 'PROVIDER_REQUIRED'
            created = c.post('/v1/realtime/sessions', json={'language': 'zh', 'translate_to': 'en', 'provider_id': 'fake-local', 'step_sec': .5})
            assert created.status_code == 201, created.text
            sid = created.json()['session_id']
            assert created.json()['sample_rate'] == 16000 and created.json()['model'] == 'turbo'
            assert c.post('/v1/realtime/sessions', json={}).status_code == 409  # 一次只開一個（顯示卡）
            headers = {'Content-Type': 'application/octet-stream'}
            assert c.post(f'/v1/realtime/sessions/{sid}/audio', content=b'\0' * 3, headers=headers).status_code == 422  # 不是整數個 16 位元樣本
            assert c.post(f'/v1/realtime/sessions/{sid}/audio', content=pcm(6), headers=headers).status_code == 413  # 一次最多 5 秒
            events = []
            for _ in range(7):
                response = c.post(f'/v1/realtime/sessions/{sid}/audio', content=pcm(.5), headers=headers)
                assert response.status_code == 200, response.text
                events += response.json()['events']
            done = c.post(f'/v1/realtime/sessions/{sid}/finish')
            assert done.status_code == 200
            final = done.json()
            events += final['events']
            assert [line['text'] for line in final['lines']] == ['大家好。', '今天玩遊戲']
            assert final['lines'][0]['translation'] == 'Hi everyone.'
            assert final['srt'].startswith('1\n00:00:00,200 --> 00:00:00,900\n大家好。')
            assert any(e['type'] == 'translation' and e['line_id'] == 'L1' for e in events)
            assert c.post(f'/v1/realtime/sessions/{sid}/audio', content=pcm(.5), headers=headers).status_code == 404  # 結束後關閉
            assert c.post('/v1/realtime/sessions', json={}).status_code == 201  # 可以再開


def test_realtime_waits_for_the_gpu_when_a_batch_job_is_running(tmp_path, monkeypatch):
    from app.studio.api import create_app
    monkeypatch.setattr(realtime, 'load_transcriber', lambda model, device, language, hints: fake_transcribe)
    app = create_app(tmp_path / 'state', start_workers=False)
    store = app.state.store
    project = store.create('project', {'name': 'gpu'})['id']
    store.submit(project, {'kind': 'align', 'transcript_revision': 't'}, 'gpu')
    store.claim('gpu')
    with TestClient(app) as c:
        c.get('/v1/session')
        busy = c.post('/v1/realtime/sessions', json={})
    assert busy.status_code == 409 and busy.json()['error']['code'] == 'GPU_BUSY'


def test_cli_streams_a_wav_file_as_a_realtime_cross_test(tmp_path, capsys, monkeypatch):
    from app.studio.cli import main
    monkeypatch.setenv('STUDIO_API_TOKEN', 'test-token')
    path = tmp_path / 'clip.wav'
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000); out.writeframes(pcm(1.2))
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith('/realtime/sessions'):
            return httpx.Response(201, json={'session_id': 'rt_1', 'sample_rate': 16000, 'model': 'turbo'})
        if request.url.path.endswith('/audio'):
            return httpx.Response(200, json={'events': [{'type': 'tentative', 'text': '大家'}], 'received_sec': 0.5})
        return httpx.Response(200, json={'events': [], 'lines': [{'id': 'L1', 'start': .2, 'end': .9, 'text': '大家好。', 'translation': 'Hi'}],
                                         'srt': '1\n00:00:00,200 --> 00:00:00,900\n大家好。\n', 'stats': {'steps': 3}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as api:
        assert main(['realtime', '--file', str(path), '--translate-to', 'en', '--provider', 'local-lmstudio', '--chunk', '0.5', '--json'], client=api) == 0
    created = json.loads(calls[0].content)
    assert created['translate_to'] == 'en' and created['provider_id'] == 'local-lmstudio'
    audio = [c for c in calls if c.url.path.endswith('/audio')]
    assert [len(c.content) for c in audio] == [16000, 16000, 6400] and audio[0].headers['content-type'] == 'application/octet-stream'
    out = json.loads(capsys.readouterr().out)
    assert out['lines'][0]['text'] == '大家好。' and out['session_id'] == 'rt_1' and out['chunks'] == 3
    assert 'first_event_sec' in out['client'] and 'wall_sec' in out['client']


def test_batch_gpu_jobs_wait_while_a_realtime_session_is_live(tmp_path, monkeypatch):
    # M7-3：即時字幕進行中，新的轉錄／對齊工作先排隊，不搶顯示卡；即時結束後照常派出
    from app.studio.api import create_app
    monkeypatch.setattr(realtime, 'load_transcriber', lambda model, device, language, hints: fake_transcribe)
    app = create_app(tmp_path / 'state', start_workers=False)
    coordinator = app.state.coordinator
    store = app.state.store
    project = store.create('project', {'name': 'gpu'})['id']
    with TestClient(app) as c:
        c.get('/v1/session')
        sid = c.post('/v1/realtime/sessions', json={}).json()['session_id']
        assert coordinator.held('gpu') is True and coordinator.held('cpu') is False
        job = store.submit(project, {'kind': 'align', 'transcript_revision': 't'}, 'gpu')
        assert coordinator.claim_next('gpu') is None and store.job(job['id'])['status'] == 'queued'
        c.delete(f'/v1/realtime/sessions/{sid}')
        assert coordinator.held('gpu') is False
        assert coordinator.claim_next('gpu')['id'] == job['id']


def test_agreement_is_by_characters_so_changing_word_splits_still_commit():
    # 2026-09-20 交叉測試：中文「字」的切法每次重轉都可能不同（「大家」／「大」「家」），逐字比對才會定稿
    splits = [[('大家', .2, .6)], [('大', .2, .4), ('家好。', .4, .9)], [('大家好。', .2, .9), ('今', 1.8, 2.0)]]
    calls = {'n': 0}

    def transcribe(samples, *, offset, prompt=''):
        rows = splits[min(calls['n'], len(splits) - 1)]
        calls['n'] += 1
        end = offset + len(samples) / 16000
        return [{'text': t, 'start': s - offset, 'end': e - offset} for t, s, e in rows if s >= offset - 1e-6 and e <= end + 1e-6]
    session = realtime.StreamingTranscriber(transcribe, step_sec=.5)
    events = []
    for _ in range(6):
        events += session.feed(np.zeros(8000, dtype=np.float32))
    assert [e['text'] for e in events if e['type'] == 'line'][:1] == ['大家好。']


def test_the_audio_window_stays_bounded_even_when_passes_never_agree():
    # 從不一致（例如音樂、雜音讓每次結果都不同）：緩衝區不能無限長，否則每一步越來越慢（交叉測試定稿延遲中位 22 秒）
    counter = {'n': 0}

    def transcribe(samples, *, offset, prompt=''):
        counter['n'] += 1
        length = len(samples) / 16000
        return [{'text': f'字{counter["n"]}{i}', 'start': float(i), 'end': float(i) + .8} for i in range(int(length))]
    session = realtime.StreamingTranscriber(transcribe, step_sec=1.0, max_window_sec=8.0)
    longest, lines = 0.0, 0
    for _ in range(60):
        events = session.feed(np.zeros(8000, dtype=np.float32))
        lines += sum(1 for e in events if e['type'] == 'line')
        longest = max(longest, len(session.buffer) / 16000)
    assert longest <= 8.0 + 1.0 and lines > 0  # 超過視窗就把較舊的暫定字強制定稿成行、丟掉舊音訊


def test_live_decoding_uses_one_temperature_and_caps_tokens_by_window_length(monkeypatch):
    # 2026-09-20 交叉測試：large-v3 即時處理時間為音訊 2.8 倍、單步最長 13.6 秒 —— 音樂／重複段觸發 Whisper 溫度重試（最多 6 次）與長輸出
    import sys
    from types import SimpleNamespace
    seen = []

    class Model:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, samples, **options):
            seen.append(options)
            return iter([]), SimpleNamespace(language='zh')
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=Model))
    transcribe = realtime.load_transcriber('turbo', 'cpu', 'zh', '彭彭, 斯斯')
    transcribe(np.zeros(16000 * 4, dtype=np.float32), offset=0.0, prompt='大家好')
    [options] = seen
    assert options['temperature'] == 0.0 and options['max_new_tokens'] <= 15 * 4 + 10
    assert options['language'] == 'zh' and options['hotwords'] == '彭彭, 斯斯' and options['word_timestamps'] is True


def test_live_chinese_lines_are_shown_in_traditional_characters():
    # 2026-09-20 冒煙：即時字幕出現簡體「我没打死」；批次流程會轉繁體，即時也要一致
    def transcribe(samples, *, offset, prompt=''):
        end = offset + len(samples) / 16000
        words = [('我', .2, .4), ('没', .4, .6), ('打', .6, .8), ('死。', .8, 1.0)]
        return [{'text': t, 'start': s - offset, 'end': e - offset} for t, s, e in words if s >= offset - 1e-6 and e <= end - .35]
    session = realtime.StreamingTranscriber(transcribe, step_sec=.5, normalize=realtime.normalizer('zh'))
    events = []
    for _ in range(4):
        events += session.feed(np.zeros(8000, dtype=np.float32))
    events += session.finish()
    assert [e['text'] for e in events if e['type'] == 'line'] == ['我沒打死。']
    assert all('没' not in e['text'] for e in events if e['type'] == 'tentative')
    assert realtime.normalizer('en')('ok') == 'ok'


def test_m7_5_default_settings_commit_unstable_speech_within_six_seconds():
    """M7-5：真跑 turbo 定稿延遲 p95 7.28 秒、最長 10.2 秒 —— 一直不一致的段落要等緩衝區滿 10 秒才強制定稿。
    預設視窗縮短後，最慢的定稿約 6 秒（視窗＋一個送音訊的顆粒）（中位不受影響：一致的字照舊兩次一致就定稿）。"""
    counter = {'n': 0}

    def transcribe(samples, *, offset, prompt=''):
        counter['n'] += 1
        length = len(samples) / 16000
        return [{'text': f'字{counter["n"]}{i}', 'start': float(i), 'end': float(i) + .8} for i in range(int(length))]
    session = realtime.StreamingTranscriber(transcribe)  # 預設值
    for _ in range(120):
        session.feed(np.zeros(8000, dtype=np.float32))
    lag = session.stats()['commit_lag_sec']
    # 上限＝視窗 6 秒＋一個送音訊的顆粒（0.5 秒）：字的結束點不會剛好落在送出的邊界上
    assert lag['max'] is not None and lag['max'] <= 6.5, lag
