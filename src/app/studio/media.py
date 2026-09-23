"""受限 YouTube 取得與 FFmpeg 媒體運算；無資料庫副作用。"""
from __future__ import annotations

from decimal import Decimal
import hashlib
import json
import os
import signal
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from .domain import TimeRange


def _run(args, timeout=60, binary=False):
    """外層 supervisor 負責整棵 worker 樹；命令不經 shell。"""
    argv = [str(a) for a in args]
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=os.name != 'nt')
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           capture_output=True, timeout=10)
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.kill()
        process.communicate(timeout=10)
        raise TimeoutError(f'{Path(str(args[0])).name} 超過 {timeout} 秒期限') from None
    if process.returncode:
        # 遠端媒體的簽章 URL 不流入診斷或 API。
        diagnostic = re.sub(r'https?://[^\s"\']+', '[remote-url]', stderr.decode('utf8', 'replace'))
        raise RuntimeError(f'{Path(str(args[0])).name} exited {process.returncode}: {diagnostic[-2500:]}')
    return stdout if binary else stdout.decode('utf8', 'replace')


def _us(value):
    return int(Decimal(str(value)) * 1000000)


def _seconds(value):
    return f'{value // 1000000}.{value % 1000000:06d}'


def _destination(path):
    path = Path(path).resolve()
    if path.exists():
        raise FileExistsError('輸出檔已存在，請使用新版本路徑')
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def ffprobe(path, timeout=30):
    data = json.loads(_run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-show_data_hash', 'sha256', '-of', 'json', Path(path)], timeout))
    duration = data.get('format', {}).get('duration')
    if duration is None:
        values = [Decimal(s['duration']) for s in data.get('streams', []) if s.get('duration')]
        if not values:
            raise ValueError('無法判定媒體時長')
        duration = max(values)
    data['duration_us'] = _us(duration)
    return data


def canonical_youtube_url(url):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('僅接受 HTTPS YouTube 影片網址')
    host = (parsed.hostname or '').lower()
    if host == 'youtu.be':
        video_id = parsed.path.strip('/')
    elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
        if parsed.path == '/watch':
            video_id = parse_qs(parsed.query).get('v', [''])[0]
        elif re.fullmatch(r'/(live|shorts|embed)/[^/]+/?', parsed.path):
            video_id = parsed.path.strip('/').split('/')[1]
        else:
            video_id = ''
    else:
        video_id = ''
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        raise ValueError('需要單一 YouTube 影片網址')
    return f'https://www.youtube.com/watch?v={video_id}'


def _yt_args():
    return [os.environ.get('STUDIO_YTDLP_PYTHON', sys.executable), '-m', 'yt_dlp', '--ignore-config', '--no-playlist', '--js-runtimes', 'node',
            '--socket-timeout', '15', '--retries', '1', '--extractor-retries', '1', '--no-progress']


def probe_youtube(url, timeout=60):
    url = canonical_youtube_url(url)
    raw = json.loads(_run([*_yt_args(), '--skip-download', '--dump-single-json', url], timeout))
    if raw.get('is_live') or raw.get('live_status') in ('is_live', 'is_upcoming'):
        raise ValueError('目前只支援已結束且有固定長度的影片')
    if not raw.get('duration'):
        raise ValueError('來源沒有可驗證的時長')
    return {'id': raw['id'], 'url': url, 'title': raw.get('title', ''), 'duration_us': _us(raw['duration']),
            'live_status': raw.get('live_status'), 'formats': [{k: f.get(k) for k in
                ('format_id', 'ext', 'acodec', 'vcodec', 'height', 'fps', 'abr', 'language')}
                for f in raw.get('formats', []) if f.get('protocol') != 'mhtml']}


def _record(path, requested=None, actual=None, status='estimated', warnings=None):
    path = Path(path)
    probe = ffprobe(path)
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    result = {'path': str(path.resolve()), 'duration_us': probe['duration_us'], 'sha256': digest,
              'size_bytes': path.stat().st_size, 'probe': probe, 'warnings': warnings or []}
    if requested:
        result['requested_range'] = requested
        result['actual_range'] = actual
        start = actual['start_us'] if actual else requested['start_us']
        result['source_map'] = {'source_start_us': start, 'source_end_us': start + probe['duration_us'],
                                'asset_start_us': 0, 'asset_end_us': probe['duration_us'], 'status': status}
    return result


# 向 googlevideo 每次最多要這麼多：開放式 Range（bytes=X-）會被限速到約 30 KB/s，分段 Range 約 24 MB/s（2026-09-21 實測，yt-dlp 也這樣做）
UPSTREAM_CHUNK_BYTES = 10 * 1024 * 1024


def _relay_range(url, headers, spec, *, send_head, write, on_bytes, chunk_bytes=UPSTREAM_CHUNK_BYTES, opener=None):
    """把 FFmpeg 要的 Range（spec＝bytes=X- 或 bytes=X-Y）拆成每次最多 chunk_bytes 的請求依序向來源取，
    回給 FFmpeg 時仍是一個完整的 206 回應（send_head 一次、write 多次）。來源不回 206 就拋 OSError。"""
    import urllib.request
    opener = opener or urllib.request.urlopen
    start_text, _, end_text = spec.split('=', 1)[1].partition('-')
    position = int(start_text)
    wanted_end = int(end_text) if end_text else None
    final_end = None
    while final_end is None or position <= final_end:
        piece_end = position + chunk_bytes - 1
        limit = wanted_end if final_end is None else final_end
        if limit is not None:
            piece_end = min(piece_end, limit)
        request = urllib.request.Request(url, headers={**headers, 'Range': f'bytes={position}-{piece_end}'})
        with opener(request, timeout=15) as remote:
            if remote.status != 206:
                raise OSError('來源沒有回應部分內容（206）')
            if final_end is None:
                total = int(remote.headers['Content-Range'].split('/')[-1])
                final_end = min(wanted_end, total - 1) if wanted_end is not None else total - 1
                send_head(content_type=remote.headers.get('Content-Type'), length=final_end - int(start_text) + 1,
                          content_range=f'bytes {start_text}-{final_end}/{total}')
            while True:
                chunk = remote.read(65536)
                if not chunk:
                    break
                on_bytes(len(chunk))
                write(chunk)
                position += len(chunk)
        if position <= piece_end:
            raise OSError('來源回傳的內容比要求的短')


def _download_section_bridge(url, selector, span, kind, folder, container, mode, timeout):
    """以本次來源的受限 Range 橋接避開部分 Windows FFmpeg HTTPS 停滯。"""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    import urllib.request
    raw = json.loads(_run([*_yt_args(), '-f', selector, '--skip-download', '--dump-single-json', url], 60))
    streams = raw.get('requested_formats') or [raw]
    if any(urlparse(item['url']).scheme != 'https' or not (urlparse(item['url']).hostname or '').endswith('.googlevideo.com') for item in streams):
        raise ValueError('Range 橋接只接受本次 YouTube 提供的 HTTPS 媒體來源')
    token = uuid4().hex
    counters = {'bytes_received': 0, 'range_requests': 0, 'byte_budget': 256 * 1024 * 1024, 'source_total_bytes': {}, 'requested_byte_ranges': []}
    lock = threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'
        def log_message(self, *args):
            pass
        def do_GET(self):
            match = re.fullmatch('/' + token + r'/(\d+)', self.path)
            if not match or int(match[1]) >= len(streams):
                self.send_error(404)
                return
            spec = self.headers.get('Range', 'bytes=0-')
            if not re.fullmatch(r'bytes=\d+-\d*', spec):
                self.send_error(416)
                return
            stream = streams[int(match[1])]
            headers = dict(stream.get('http_headers', raw.get('http_headers', {})))

            def send_head(content_type, length, content_range):
                self.send_response(206)
                if content_type:
                    self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(length))
                self.send_header('Content-Range', content_range)
                self.send_header('Accept-Ranges', 'bytes')
                self.end_headers()
                with lock:
                    counters['range_requests'] += 1
                    counters['source_total_bytes'][str(int(match[1]))] = int(content_range.split('/')[-1])
                    counters['requested_byte_ranges'].append({'stream': int(match[1]), 'range': spec})

            def on_bytes(size):
                with lock:
                    counters['bytes_received'] += size
                    if counters['bytes_received'] > counters['byte_budget']:
                        raise RuntimeError('區段取得超過本次傳輸預算')

            def write(chunk):
                self.wfile.write(chunk)
                self.wfile.flush()
            try:
                # 分段向來源取（每次最多 10 MB），避開開放式 Range 的限速
                _relay_range(stream['url'], headers, spec, send_head=send_head, write=write, on_bytes=on_bytes)
            except (OSError, RuntimeError, ValueError):
                self.close_connection = True
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    extension = container if container != 'source' else ('mp4' if kind == 'video' else streams[0].get('ext', 'm4a'))
    target = folder / f'media.{extension}'
    args = ['ffmpeg', '-v', 'error', '-nostdin', '-n']
    for index in range(len(streams)):
        args += ['-ss', _seconds(span.start_us), '-rw_timeout', '20000000', '-i', f'http://127.0.0.1:{server.server_port}/{token}/{index}']
    args += ['-t', _seconds(span.end_us-span.start_us)]
    if kind == 'audio':
        args += ['-map', '0:a:0', '-vn']
    else:
        video_index = next((i for i,f in enumerate(streams) if f.get('vcodec') != 'none'), 0)
        audio_index = next((i for i,f in enumerate(streams) if f.get('acodec') != 'none'), 0)
        args += ['-map', f'{video_index}:v:0', '-map', f'{audio_index}:a:0']
    if mode == 'copy' and extension != 'mp3':
        args += ['-c', 'copy']
    else:
        if kind == 'video':
            args += ['-c:v', 'libx264', '-preset', 'fast', '-crf', '18']
        args += ['-c:a', 'libmp3lame' if extension == 'mp3' else ('libopus' if extension in ('opus','webm') else 'aac')]
    try:
        _run([*args, str(target)], timeout)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    if counters['bytes_received'] > counters['byte_budget']:
        raise RuntimeError('區段下載超過傳輸預算')
    counters['command'] = [re.sub(r'http://127.0.0.1:[^ ]+', '[ephemeral-range-stream]', str(a)) for a in [*args, str(target)]]
    counters['format_ids'] = [f.get('format_id') for f in streams]
    return counters


def download_youtube(url, ranges, kind, output_dir, format_policy=None, timeout=1200, on_asset=None, boundary_policy="source_seek"):
    """每個請求區段各產生一檔；回呼可立即發布音訊 ready。"""
    if kind not in ('audio', 'video'):
        raise ValueError('kind 必須是 audio 或 video')
    policy = format_policy.model_dump() if hasattr(format_policy, 'model_dump') else dict(format_policy or {})
    if set(policy) - {'container', 'max_height', 'max_fps', 'audio_track_id', 'audio_bitrate_kbps', 'allow_transcode'}:
        raise ValueError('不支援的下載規格欄位')
    height = policy.get('max_height')
    fps = policy.get('max_fps')
    if height is not None and (type(height) is not int or not 1 <= height <= 16384):
        raise ValueError('不支援的影片高度')
    if fps is not None and (type(fps) is not int or not 1 <= fps <= 240):
        raise ValueError('不支援的影片幀率')
    container = policy.get('container', 'source')
    allowed = ('m4a', 'mp3', 'wav', 'opus', 'webm') if kind == 'audio' else ('mp4', 'mkv', 'webm')
    if container != 'source' and container not in allowed:
        raise ValueError('不支援的媒體容器')
    bitrate = policy.get('audio_bitrate_kbps')
    if bitrate is not None and (type(bitrate) is not int or not 32 <= bitrate <= 512):
        raise ValueError('不支援的音訊 bitrate')
    if type(policy.get('allow_transcode', False)) is not bool:
        raise ValueError('allow_transcode 必須為布林值')
    if bitrate is not None and container == 'source':
        raise ValueError('指定 bitrate 時必須同時指定輸出容器')
    if (bitrate is not None or container in ('mp3', 'wav')) and not policy.get('allow_transcode', False):
        raise ValueError('指定音訊 bitrate／轉換格式需要 allow_transcode')
    if boundary_policy not in ('source_seek', 'accurate'):
        raise ValueError('boundary_policy 必須是 source_seek 或 accurate')
    mode = 'accurate' if boundary_policy == 'accurate' else 'copy'
    source = probe_youtube(url)
    spans = [r if isinstance(r, TimeRange) else TimeRange.model_validate(r) for r in ranges]
    if not spans:
        spans = [TimeRange(start_us=0, end_us=source['duration_us'])]
    if any(r.end_us > source['duration_us'] for r in spans):
        raise ValueError('下載區段超出來源時長')
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    results = []
    for index, span in enumerate(spans):
        folder = root / f'section-{index + 1}-{uuid4().hex}'
        folder.mkdir()
        audio_selector = 'bestaudio'
        track = policy.get('audio_track_id')
        if track:
            available = [f for f in source['formats'] if f['format_id'] == track and f.get('acodec') != 'none' and f.get('vcodec') == 'none']
            if not available:
                raise ValueError('指定 audio_track_id 不在來源獨立音軌格式清單')
            audio_selector = str(track)
        elif container == 'm4a' or (kind == 'video' and container == 'mp4'):
            audio_selector += '[ext=m4a]'
        video_selector = 'bestvideo'
        combined_selector = 'best'
        for key, value in [('height', height), ('fps', fps)]:
            if value is not None:
                video_selector += f'[{key}<={value}]'
                combined_selector += f'[{key}<={value}]'
        if container == 'mp4':
            video_selector += '[vcodec^=avc1]'
            combined_selector += '[ext=mp4]'
        selector = audio_selector if kind == 'audio' else f'{video_selector}+{audio_selector}'
        if not track and kind == 'video':
            selector += '/' + combined_selector
        args = [*_yt_args(), '-f', selector, '--download-sections', f'*{_seconds(span.start_us)}-{_seconds(span.end_us)}',
                '-o', str(folder / 'media.%(ext)s'), '--no-overwrites',
                '--downloader-args', 'ffmpeg_i:-rw_timeout 15000000']
        if mode == 'accurate':
            args += ['--force-keyframes-at-cuts']
        if kind == 'audio' and container != 'source':
            args += ['-x', '--audio-format', container]
            if bitrate:
                args += ['--audio-quality', f'{bitrate}K']
        elif kind == 'video' and container != 'source':
            args += ['--merge-output-format', container, '--remux-video', container]
        transport = None
        if os.environ.get('STUDIO_MEDIA_HTTP_BRIDGE', '1' if os.name == 'nt' else '0') == '1':
            transport = _download_section_bridge(source['url'], selector, span, kind, folder, container, mode, timeout)
        else:
            _run([*args, source['url']], timeout)
        files = [p for p in folder.glob('media.*') if p.suffix[1:] in allowed and p.stat().st_size]
        if len(files) != 1:
            raise RuntimeError('下載器未產生唯一完整媒體')
        selected_path = files[0]
        if bitrate is not None:
            target = folder / f'media-bitrate.{container}'
            codec = 'libmp3lame' if container == 'mp3' else 'aac'
            args_bitrate = ['ffmpeg', '-v', 'error', '-nostdin', '-n', '-i', str(selected_path)]
            if kind == 'video':
                args_bitrate += ['-map', '0:v:0?', '-c:v', 'copy']
            args_bitrate += ['-map', '0:a:0', '-c:a', codec, '-b:a', f'{bitrate}k', str(target)]
            _run(args_bitrate, timeout)
            selected_path = target
        requested = span.model_dump()
        requested_duration = span.end_us - span.start_us
        probe_duration = ffprobe(selected_path)['duration_us']
        actual = None
        status = 'estimated'
        warnings = ['遠端區段來源偏移依下載請求估計，尚無原片 PTS 對照證據；時長相符不代表已驗證同步']
        if mode == 'copy' and probe_duration > requested_duration + 250000:
            # copy（source_seek）模式會從請求起點之前的關鍵影格／片段開始，但終點精準（2026-09-18 互相關實測：早 9.98 秒，時長差 9.99 秒）。
            # 起點以「請求終點 − 實際時長」估計，避免整段時間軸提前。
            actual = {'start_us': span.end_us - probe_duration, 'end_us': span.end_us}
            status = 'estimated_from_duration'
            warnings = [f'關鍵影格尋址：實際起點比請求早約 {(probe_duration - requested_duration) / 1e6:.2f} 秒，來源對映已依實際時長修正；要精準起點請改用 accurate']
        result = _record(selected_path, requested=requested, actual=actual, status=status, warnings=warnings)
        if (mode == 'accurate' and abs(result['duration_us'] - requested_duration) > 250000) or (mode == 'copy' and result['duration_us'] < requested_duration - 1000000):
            raise RuntimeError('取得時長不符合請求區段，可能網路中斷；不發布不完整媒體')
        result.update({'kind': kind, 'source': source, 'format_policy': policy, 'section_index': index,
                       'acquisition_mode': mode, 'command': transport['command'] if transport else args, 'transport': transport})
        results.append(result)
        if on_asset:
            on_asset(result)
    return results


def keyframes(path, timeout=60):
    data = json.loads(_run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-skip_frame', 'nokey',
                           '-show_frames', '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', path], timeout))
    return [_us(f['best_effort_timestamp_time']) for f in data.get('frames', []) if f.get('best_effort_timestamp_time') is not None]


def cut_media(path, output, start_us, end_us, mode='accurate', kind='video', timeout=600):
    span = TimeRange(start_us=start_us, end_us=end_us)
    output = _destination(output)
    source = ffprobe(path)
    if end_us > source['duration_us']:
        raise ValueError('裁切範圍超出媒體時長')
    if mode not in ('accurate', 'copy') or kind not in ('video', 'audio'):
        raise ValueError('不支援的裁切模式或媒體類型')
    warnings = []
    actual_start = start_us
    has_video = any(s['codec_type'] == 'video' for s in source['streams']) and kind == 'video'
    if mode == 'copy' and has_video:
        points = keyframes(path)
        prior = [p for p in points if p <= start_us]
        if not prior:
            raise ValueError('沒有可確認的前置關鍵影格，請使用精準模式')
        actual_start = max(prior)
        warnings.append('快速複製從前置關鍵影格開始；尾端依封包邊界，時間對映仍待核對')
    args = ['ffmpeg', '-v', 'error', '-nostdin', '-n', '-ss', _seconds(actual_start), '-i', str(path),
            '-t', _seconds(end_us - actual_start)]
    if kind == 'audio':
        args += ['-map', '0:a:0', '-vn']
    else:
        args += ['-map', '0:v:0?', '-map', '0:a:0?']
    if mode == 'copy':
        args += ['-c', 'copy', '-avoid_negative_ts', 'make_zero']
    elif kind == 'audio':
        codec = {'.wav': 'pcm_s16le', '.mp3': 'libmp3lame', '.opus': 'libopus'}.get(output.suffix.lower(), 'aac')
        args += ['-c:a', codec]
        if codec == 'pcm_s16le':
            args += ['-ar', '16000', '-ac', '1']
    else:
        args += ['-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p', '-c:a', 'aac']
    if output.suffix.lower() in ('.mp4', '.m4a', '.mov'):
        args += ['-movflags', '+faststart']
    _run([*args, str(output)], timeout)
    # 即使本機 accurate 成功，沒有影格／音訊真值比對也不升為 verified。
    if mode == 'accurate':
        warnings.append('精準重編碼已執行，實際影格邊界仍受來源時基限制')
    actual = {'start_us': actual_start, 'end_us': actual_start + ffprobe(output)['duration_us']}
    result = _record(output, span.model_dump(), actual, warnings=warnings)
    result['mode'] = mode
    return result


def _signature(probe):
    return [(s.get('codec_type'), s.get('codec_name'), s.get('width'), s.get('height'), s.get('sample_rate'),
             s.get('channels'), s.get('time_base'), s.get('profile'), s.get('pix_fmt'), s.get('extradata_hash')) for s in probe['streams']]


def merge_media(paths, output, mode='accurate', timeout=600):
    if not paths:
        raise ValueError('至少需要一段媒體')
    if mode not in ('copy', 'accurate'):
        raise ValueError('不支援的合併模式')
    output = _destination(output)
    paths = [str(Path(p).resolve()) for p in paths]
    probes = [ffprobe(p) for p in paths]
    video = any(s['codec_type'] == 'video' for s in probes[0]['streams'])
    audio = any(s['codec_type'] == 'audio' for s in probes[0]['streams'])
    if any(any(s['codec_type'] == 'video' for s in p['streams']) != video or
           any(s['codec_type'] == 'audio' for s in p['streams']) != audio for p in probes):
        raise ValueError('音視訊軌道配置不同，請先轉成相同配置')
    if mode == 'copy' and any(_signature(p) != _signature(probes[0]) for p in probes[1:]):
        raise ValueError('codec／時基不同，不能直接複製合併')
    if mode == 'accurate':
        args = ['ffmpeg', '-v', 'error', '-nostdin', '-n']
        for path in paths:
            args += ['-i', path]
        filters, labels = [], []
        first_video = next((s for s in probes[0]['streams'] if s['codec_type'] == 'video'), None)
        for index, probe in enumerate(probes):
            if video:
                w, h = first_video['width'], first_video['height']
                filters.append(f'[{index}:v:0]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,settb=AVTB,setpts=PTS-STARTPTS[v{index}]')
                labels.append(f'[v{index}]')
            if audio:
                filters.append(f'[{index}:a:0]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[a{index}]')
                labels.append(f'[a{index}]')
        filters.append(''.join(labels) + f'concat=n={len(paths)}:v={int(video)}:a={int(audio)}' + ('[vout]' if video else '') + ('[aout]' if audio else ''))
        args += ['-filter_complex', ';'.join(filters)]
        if video:
            args += ['-map', '[vout]', '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p', '-fps_mode', 'vfr']
        if audio:
            codec = {'.wav': 'pcm_s16le', '.mp3': 'libmp3lame', '.opus': 'libopus'}.get(output.suffix.lower(), 'aac')
            args += ['-map', '[aout]', '-c:a', codec]
        _run([*args, str(output)], timeout)
    else:
        with tempfile.TemporaryDirectory(prefix='dongbi-merge-', dir=output.parent) as folder:
            listing = Path(folder) / 'inputs.txt'
            if any('\n' in p or '\r' in p for p in paths):
                raise ValueError('檔案路徑不能包含換行')
            listing.write_text(''.join("file '" + p.replace('\\', '/').replace("'", "'\\''") + "'\n" for p in paths), encoding='utf8')
            _run(['ffmpeg', '-v', 'error', '-nostdin', '-n', '-f', 'concat', '-safe', '0', '-i', listing,
                  '-map', '0', '-c', 'copy', str(output)], timeout)
    result = _record(output, warnings=['合併長度含編碼封包容差，輸出時基需依 manifest 使用'])
    result.update({'inputs': paths, 'mode': mode})
    return result


def waveform_peaks(path, buckets=2000, timeout=120):
    if type(buckets) is not int or not 1 <= buckets <= 20000:
        raise ValueError('波形 bucket 範圍為 1–20000')
    import array
    values = array.array('h')
    values.frombytes(_run(['ffmpeg', '-v', 'error', '-i', path, '-vn', '-ac', '1', '-ar', '8000',
                           '-f', 's16le', 'pipe:1'], timeout, binary=True))
    stride = max(1, (len(values) + buckets - 1) // buckets)
    return {'peaks': [max(abs(x) for x in values[i:i+stride]) / 32768 for i in range(0, len(values), stride)],
            'duration_us': len(values) * 1000000 // 8000, 'sample_rate': 8000}
