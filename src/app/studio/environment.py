"""環境報告：服務內呼叫啟動器的檢查（bootstrap.checks）並加上套件與路徑狀態，供 GET /v1/environment 與 doctor --env 使用。"""
import importlib.metadata
import sys

from app import config as app_config

if str(app_config.PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(app_config.PROJECT_ROOT))

from bootstrap import checks, deps, lockfile, models as bootstrap_models  # noqa: E402


def _installed_packages():
    installed = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata["Name"]
        if name:
            installed[lockfile.normalize_name(name)] = distribution.version
    return installed


def _packages(installed=None):
    """比對鎖定清單；選用群組（engine_b、v1）的缺件標 optional_missing，不算環境問題。"""
    lock_path = app_config.PROJECT_ROOT / "requirements.lock"
    if not lock_path.is_file():
        return {"lock": None, "items": []}
    entries = lockfile.load_lock(lock_path)
    if installed is None:
        installed = _installed_packages()
    items = []
    for entry in entries:
        current = installed.get(entry["name"])
        optional = entry["group"] in lockfile.OPTIONAL_GROUPS
        if current is None:
            status = "optional_missing" if optional else "missing"
        elif entry["version"] is not None and deps.base_version(current) != entry["version"]:
            status = "version_mismatch"
        else:
            status = "ok"
        items.append(dict(name=entry["name"], group=entry["group"], required=entry["version"], installed=current, status=status, optional=optional))
    return {"lock": str(lock_path), "items": items}


def _models():
    manifest_path = app_config.PROJECT_ROOT / "models.manifest.json"
    if not manifest_path.is_file():
        return {"manifest": None, "missing": []}
    manifest = bootstrap_models.load_manifest(manifest_path)
    return {"manifest": str(manifest_path), "missing": bootstrap_models.missing_models(manifest, app_config.MODELS_DIR),
            "optional_missing": [item for item in bootstrap_models.missing_models(manifest, app_config.MODELS_DIR, include_optional=True)
                                 if item not in bootstrap_models.missing_models(manifest, app_config.MODELS_DIR)]}


def environment_report():
    """整合報告；不含金鑰，路徑為專案相對解析後的實際值。"""
    report = checks.collect_env()
    report["python_executable"] = sys.executable
    report["packages"] = _packages()
    report["models"] = _models()
    report["paths"] = dict(project_root=str(app_config.PROJECT_ROOT), data_dir=app_config.DATA_DIR, models_dir=app_config.MODELS_DIR,
                           hf_cache=app_config.HF_CACHE_DIR, torch_home=app_config.TORCH_HOME_DIR, gguf_dir=app_config.GGUF_DIR)
    return report
