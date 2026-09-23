"""在已安裝套件的專案 Python 執行本機模型準備；不需 API 先啟動。"""
import argparse
import json
import os
from pathlib import Path
import sys

LARGE_V3 = 'hf:Systran/faster-whisper-large-v3'


def check_runtime():
    # 套件可匯入才開始幾 GB 的下載；不是 GPU 推論驗收。
    import whisperx  # noqa: F401
    import whisperx.alignment  # noqa: F401
    import faster_whisper  # noqa: F401
    import torch  # noqa: F401
    import torchaudio  # noqa: F401
    import transformers  # noqa: F401


def local_plan(manifest, models_dir):
    from app.studio import model_store
    plan = model_store.plan_download(manifest, models_dir)
    plan['items'].sort(key=lambda item: item['id'] != LARGE_V3)
    if not any(item['id'] == LARGE_V3 for item in plan['items']):
        raise ValueError('LARGE_V3_REQUIRED')
    return plan


def prepare_models(models_dir):
    from app.studio import model_store
    manifest = model_store.load_manifest()
    plan = local_plan(manifest, models_dir)
    def progress(event):
        print(json.dumps(event, ensure_ascii=False), file=sys.stderr, flush=True)
    result = model_store.run_download(plan, progress=progress)
    if any(item['status'] not in ('present', 'downloaded', 'copied', 'converted') for item in result['items']):
        raise RuntimeError('MODEL_PREPARATION_FAILED')
    state = model_store.check_models(manifest, models_dir)
    if not state['offline_ready']:
        raise RuntimeError('MODEL_RECHECK_FAILED')
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description='準備 WhisperX 核心及必要本機模型')
    parser.add_argument('--models-dir', required=True)
    parser.add_argument('--confirm', action='store_true')
    args = parser.parse_args(argv)
    if not args.confirm:
        print('請先確認下載量與安裝位置，再加 --confirm。', file=sys.stderr)
        return 2
    base = Path(args.models_dir).resolve()
    # 明確固定下載與推論在同一目錄；若使用者另設快取，不偷偷覆寫。
    for name, target in [('HF_HUB_CACHE', base / 'hf'), ('TORCH_HOME', base / 'torch')]:
        if os.environ.get(name) and Path(os.environ[name]).resolve() != target:
            print(f'{name} 與本機方案位置不同；請先確認並統一設定。', file=sys.stderr)
            return 2
        os.environ[name] = str(target)
    os.environ['STUDIO_MODELS_DIR'] = str(base)
    try:
        print('[核心] 檢查 WhisperX、faster-whisper、torch 與對齊依賴', file=sys.stderr, flush=True)
        check_runtime()
        state = prepare_models(base)
        print(json.dumps(dict(status='ready', models_dir=str(base), **state), ensure_ascii=False))
        return 0
    except Exception as error:
        print(f'本機準備失敗：{type(error).__name__}；請檢查套件、網路與磁碟，不會啟動工作台。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
