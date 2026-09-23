"""供應者金鑰只寫入本機資料目錄的 secrets 檔；不進 config.json、log、artifact 或任何 API 回應。

讀取順序：config 指定的環境變數有值時優先，否則讀 secrets 檔；兩者皆無回 None，由呼叫端決定是否算缺金鑰。
"""
import json
import os
import re
from pathlib import Path

from .providers import ProviderError

_PROVIDER_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
_MAX_SECRET_LENGTH = 4096


def secrets_path(root):
    return Path(root) / 'secrets.json'


def _validate_id(provider_id):
    if not isinstance(provider_id, str) or not _PROVIDER_ID.match(provider_id):
        raise ProviderError('INVALID_PROVIDER_ID')


def load_secrets(root):
    path = secrets_path(root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        raise ProviderError('SECRETS_FILE_UNREADABLE') from None
    return data if isinstance(data, dict) else {}


def _write(root, data):
    # 先寫暫存再原子替換，避免寫到一半留下半檔
    path = secrets_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(path.name + '.' + os.urandom(6).hex() + '.part')
    staging.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding='utf-8')
    from .fsutil import replace_with_retry
    replace_with_retry(staging, path)


def set_secret(root, provider_id, value):
    _validate_id(provider_id)
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_SECRET_LENGTH or any(c in value for c in '\r\n\t'):
        raise ProviderError('INVALID_SECRET')
    data = load_secrets(root)
    data[provider_id] = value.strip()
    _write(root, data)


def delete_secret(root, provider_id):
    _validate_id(provider_id)
    data = load_secrets(root)
    if provider_id in data:
        del data[provider_id]
        _write(root, data)


def get_secret(root, provider_id):
    _validate_id(provider_id)
    value = load_secrets(root).get(provider_id)
    return value if isinstance(value, str) and value else None


def secret_configured(root, provider_id):
    return get_secret(root, provider_id) is not None


def resolve_secret(config, root):
    """環境變數優先，其次 secrets 檔；都沒有回 None。"""
    if not isinstance(config, dict):
        return None
    env_name = config.get('api_key_env')
    if isinstance(env_name, str) and env_name:
        value = os.environ.get(env_name)
        if value:
            return value
    identity = config.get('id')
    if isinstance(identity, str) and _PROVIDER_ID.match(identity):
        return get_secret(root, identity)
    return None
