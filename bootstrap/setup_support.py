"""首次安裝的標準函式庫工具：規劃、系統工具與獨立下載環境。"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import json
import urllib.request
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor

from .process import run_visible


def describe(state, root, local, log):
    from .run import models_dir
    log('專案環境：' + str(Path(root) / '.venv'))
    log('下載環境：' + str(Path(root) / '.venv-download'))
    log('下載器檢查：' + ('可用' if state.get('downloader_ready') else '尚未驗證或需建立／補裝'))
    log('模型位置：' + str(models_dir(root).resolve()))
    log('系統缺件：' + (', '.join(state['tools_missing']) or '無'))
    log('Python 套件依 requirements.lock 補缺／修正版本；含 WhisperX 與大型 torch wheel。')
    from . import lockfile, deps
    entries = lockfile.load_lock(Path(root) / 'requirements.lock')
    pending = deps.plan_installs(entries, state['installed'], state['groups'])
    log('待安裝／調整套件：' + (', '.join(item['spec'] for item in pending) or '無'))
    log('模型缺件：' + (', '.join(state['models_missing']) if local and state['models_missing'] else '無或本次不下載'))
    log('首次本機方案需多 GB；套件／未標大小的模型下載量未知，不把未知當作 0。')
    log('磁碟剩餘：%.1f GiB；還需安裝解壓／快取空間。' % (shutil.disk_usage(root).free / 2**30))


def estimate_models(root, missing):
    """只查官方模型中繼資料，不下載權重；無大小資訊保持 null。"""
    from .models import load_manifest
    manifest = load_manifest(Path(root) / 'models.manifest.json')
    entries = {**{'hf:' + e['repo']: e for e in manifest.get('hf', [])},
               **{'torch:' + e['file']: e for e in manifest.get('torch', [])}}
    def estimate(identity):
        entry = entries.get(identity, {})
        size = entry.get('expected_bytes')
        if not size and identity.startswith('hf:'):
            try:
                url = 'https://huggingface.co/api/models/' + quote(entry['repo'], safe='/') + '?blobs=true'
                with urllib.request.urlopen(url, timeout=10) as response:
                    siblings = json.load(response).get('siblings', [])
                if siblings and all(isinstance(row.get('size'), int) for row in siblings):
                    size = sum(row['size'] for row in siblings)
            except (OSError, ValueError, KeyError):
                pass
        return dict(id=identity, download_bytes=size or None)
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(estimate, missing))


def install_tools(names):
    if os.name != 'nt' or not shutil.which('winget'):
        raise RuntimeError('WINGET_UNAVAILABLE')
    packages = []
    for name in names:
        identity = {'ffmpeg': 'Gyan.FFmpeg', 'ffprobe': 'Gyan.FFmpeg', 'node': 'OpenJS.NodeJS.LTS'}[name]
        if identity not in packages:
            packages.append(identity)
    for identity in packages:
        run_visible(['winget', 'install', '--id', identity, '--exact', '--source', 'winget',
                     '--accept-source-agreements', '--accept-package-agreements'], label=identity)
    # 只更新本程序 PATH，讓新安裝的工具立即可被重新偵測；不寫系統環境變數。
    import winreg
    paths = [os.environ.get('PATH', '')]
    for hive, key in [(winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'),
                      (winreg.HKEY_CURRENT_USER, 'Environment')]:
        try:
            with winreg.OpenKey(hive, key) as handle:
                paths.append(os.path.expandvars(winreg.QueryValueEx(handle, 'Path')[0]))
        except OSError:
            pass
    os.environ['PATH'] = os.pathsep.join(paths)


def downloader_python(root):
    python = Path(root) / '.venv-download' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    custom = os.environ.get('STUDIO_YTDLP_PYTHON')
    if custom:
        python = Path(custom)
    return python


def downloader_ready(root):
    python = downloader_python(root)
    if not python.is_file():
        return False
    try:
        return subprocess.run([str(python), '-c', 'import yt_dlp, yt_dlp_ejs'], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def prepare_downloader(root):
    python = downloader_python(root)
    custom = os.environ.get('STUDIO_YTDLP_PYTHON')
    probe = [str(python), '-c', 'import yt_dlp, yt_dlp_ejs']
    if downloader_ready(root):
        return
    if custom:
        raise RuntimeError('CUSTOM_DOWNLOADER_NEEDS_SETUP')
    if not python.exists():
        run_visible([sys.executable, '-m', 'venv', str(Path(root) / '.venv-download')], timeout=600, label='下載環境')
    run_visible([str(python), '-m', 'pip', 'install', '--no-input', '--progress-bar', 'on', 'yt-dlp[default]'], label='yt-dlp')
    subprocess.run(probe, check=True, timeout=30)
