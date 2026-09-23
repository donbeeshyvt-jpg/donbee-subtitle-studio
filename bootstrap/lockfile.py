"""解析 requirements.lock：name==version 行、「# group: 名稱」切換群組、「-e 路徑」可編輯安裝。"""
from pathlib import Path
import re

_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s#]+)")
_GROUP = re.compile(r"^#\s*group:\s*([A-Za-z0-9_-]+)")

# 選用群組：缺件不算環境問題，由使用者啟用時再安裝（engine_b＝VibeVoice、v1＝舊版桌面介面）
OPTIONAL_GROUPS = ("engine_b", "v1")


def normalize_name(name):
    return name.strip().lower().replace("_", "-")


def editable_name(path):
    """可編輯安裝以資料夾名推導套件名（VibeVoice-main → vibevoice）。"""
    base = Path(path).name
    if base.lower().endswith("-main"):
        base = base[:-5]
    return normalize_name(base)


def parse_lock(text):
    entries, group = [], "default"
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        found = _GROUP.match(line)
        if found:
            group = found.group(1)
            continue
        if line.startswith("#"):
            continue
        if line.startswith("-e "):
            path = line[3:].strip()
            entries.append(dict(name=editable_name(path), version=None, group=group, editable=path, spec=line))
            continue
        pinned = _PIN.match(line)
        if not pinned:
            raise ValueError("INVALID_LOCK_LINE: " + line)
        name, version = normalize_name(pinned.group(1)), pinned.group(2)
        entries.append(dict(name=name, version=version, group=group, editable=None, spec=f"{name}=={version}"))
    return entries


def groups(entries):
    seen = []
    for entry in entries:
        if entry["group"] not in seen:
            seen.append(entry["group"])
    return seen


def load_lock(path):
    return parse_lock(Path(path).read_text(encoding="utf-8"))
