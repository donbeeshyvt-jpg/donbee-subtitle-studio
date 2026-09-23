"""2026-09-21 真跑：10 分鐘直播音訊下載要 330 秒。實測同一個 googlevideo 網址：開放式 Range（bytes=X-）只有 30 KB/s（被限速），
分段 Range（bytes=X-X+10MB）24 MB/s。下載橋接（Windows 預設）改成每次最多向來源要 10 MB、依序接起來給 FFmpeg。
這裡用本機替身當來源，確認分段方式、回給 FFmpeg 的標頭與內容完全正確。"""
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.studio import media

BODY = bytes(range(256)) * 14  # 3584 bytes


@contextmanager
def upstream():
    ranges = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'

        def log_message(self, *args):
            pass

        def do_GET(self):
            spec = self.headers['Range'].split('=')[1]
            start, _, end = spec.partition('-')
            start, end = int(start), int(end) if end else len(BODY) - 1
            end = min(end, len(BODY) - 1)
            ranges.append((start, end))
            data = BODY[start:end + 1]
            self.send_response(206)
            self.send_header('Content-Type', 'audio/webm')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Content-Range', f'bytes {start}-{end}/{len(BODY)}')
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f'http://127.0.0.1:{server.server_address[1]}/media', ranges
    finally:
        server.shutdown()
        server.server_close()


def relay(url, spec, chunk):
    head, data, counted = {}, bytearray(), []
    media._relay_range(url, {'User-Agent': 'x'}, spec, send_head=lambda **h: head.update(h), write=data.extend,
                       on_bytes=counted.append, chunk_bytes=chunk)
    return head, bytes(data), sum(counted)


def test_open_ended_range_is_fetched_in_small_pieces_and_relayed_as_one_response():
    with upstream() as (url, ranges):
        head, data, counted = relay(url, 'bytes=100-', 1000)
    assert ranges == [(100, 1099), (1100, 2099), (2100, 3099), (3100, 3583)]  # 每次最多 1000 bytes（正式是 10 MB）
    assert data == BODY[100:] and counted == len(BODY) - 100
    assert head == {'content_type': 'audio/webm', 'length': len(BODY) - 100, 'content_range': f'bytes 100-{len(BODY) - 1}/{len(BODY)}'}


def test_a_closed_range_smaller_than_a_piece_is_one_request():
    with upstream() as (url, ranges):
        head, data, _ = relay(url, 'bytes=10-20', 1000)
    assert ranges == [(10, 20)] and data == BODY[10:21] and head['length'] == 11
    assert head['content_range'] == f'bytes 10-20/{len(BODY)}'


def test_the_default_piece_is_ten_megabytes():
    assert media.UPSTREAM_CHUNK_BYTES == 10 * 1024 * 1024


def test_a_source_that_does_not_answer_with_partial_content_is_refused():
    class Plain(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Length', '3')
            self.end_headers()
            self.wfile.write(b'abc')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Plain)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(OSError):
            relay(f'http://127.0.0.1:{server.server_address[1]}/media', 'bytes=0-', 1000)
    finally:
        server.shutdown()
        server.server_close()
