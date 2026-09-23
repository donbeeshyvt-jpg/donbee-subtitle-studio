"""把本機既有的 Hugging Face／torch 模型快取複製進專案 models/（只複製、不刪來源）。

用法（專案根，使用模型環境的 python）：
  python scripts/import-local-models.py [--dry-run] [--include-optional] [--source-hf DIR] [--source-torch DIR]
輸出 JSON 計畫與結果；大小以 GB 顯示。
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app import config  # noqa: E402
from app.studio import model_store  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="複製本機模型快取進專案 models/")
    parser.add_argument("--dry-run", action="store_true", help="只列出計畫，不複製")
    parser.add_argument("--include-optional", action="store_true", help="連選用模型（例如 VibeVoice-ASR）一起複製")
    parser.add_argument("--source-hf", default=str(model_store.default_source_hf()))
    parser.add_argument("--source-torch", default=str(model_store.default_source_torch()))
    parser.add_argument("--manifest", default=str(model_store.manifest_path()))
    args = parser.parse_args()
    manifest = model_store.load_manifest(args.manifest)
    plan = model_store.plan_import(manifest, config.MODELS_DIR, source_hf=args.source_hf, source_torch=args.source_torch,
                                   include_optional=args.include_optional)
    print(json.dumps(dict(plan, total_gb=round(plan["total_bytes"] / 1024 ** 3, 2)), ensure_ascii=False, indent=1))
    if args.dry_run:
        return 0
    def progress(item):
        print(f"複製中：{item['id']}（{round(item['bytes'] / 1024 ** 3, 2)} GB）", file=sys.stderr, flush=True)
    result = model_store.run_import(plan, progress=progress)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    bad = [item for item in result["items"] if item.get("status") == "size_mismatch"]
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
