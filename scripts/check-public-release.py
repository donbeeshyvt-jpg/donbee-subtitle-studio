"""檢查 Git 暫存內容，避免把開發紀錄、素材、模型或常見金鑰提交。"""
import argparse
from pathlib import PurePosixPath
import re
import subprocess

ROOT_FILES = {".gitignore", ".env.example", "README.md", "README.en.md", "AGENTS.md",
              "THIRD_PARTY_NOTICES.md", "requirements.lock", "requirements-studio.txt",
              "models.manifest.json", "Start-DonBee-Subtitle-Studio.bat"}
SCRIPT_FILES = {"scripts/check-public-release.py", "scripts/import-local-models.py",
                "scripts/git-hooks/pre-commit", "scripts/git-hooks/pre-commit.ps1"}
SECRET = re.compile(rb"(?:sk-or-v1-[a-zA-Z0-9]{24,}|gh[pousr]_[a-zA-Z0-9]{30,}|"
                    rb"github_pat_[a-zA-Z0-9_]{40,}|sk-[a-zA-Z0-9_-]{32,}|"
                    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")
MAX_BYTES = 10 * 1024 * 1024


def inspect_file(name, data):
    """只回報路徑與錯誤種類，不把匹配到的金鑰印出來。"""
    path = PurePosixPath(name)
    allowed = (name in ROOT_FILES or name in SCRIPT_FILES
               or name == ".github/workflows/public-checks.yml"
               or name.startswith(".agents/skills/subtitle-studio/")
               or path.parts[0] in {"src", "bootstrap", "frontend", "tests", "guides"})
    blocked = (any(p in {"node_modules", "__pycache__", "test-artifacts", ".pytest_cache"} for p in path.parts)
               or path.name in {"TEST_REPORT.md", "CONVERSATION_LOG.md", "HANDOFF.md", "DEV_LOG.md", "secrets.json"}
               or path.name.startswith(".env") and name != ".env.example"
               or path.suffix.lower() in {".mp4", ".mkv", ".mov", ".webm", ".avi", ".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".srt", ".vtt", ".ass", ".ssa", ".gguf", ".bin", ".safetensors", ".pt", ".pth", ".sqlite3", ".pyc", ".log"})
    errors = []
    if not allowed or blocked:
        errors.append(f"{name}: 非公開程式檔案，請從暫存區移除（保留本機檔）。")
    if len(data) > MAX_BYTES:
        errors.append(f"{name}: 超過 10 MiB，請確認不是素材／模型。")
    if SECRET.search(data):
        errors.append(f"{name}: 疑似金鑰，請移除並視情況撤銷金鑰。")
    return errors


def check_sync(changed):
    if any(p.startswith(("src/", "bootstrap/", "frontend/src/")) for p in changed) and "guides/CODE_MAP.md" not in changed:
        return ["程式有變更：請同步 guides/CODE_MAP.md 並加入暫存；本機 docs 索引仍另行維護，不可提交。"]
    return []


def git(*args):
    return subprocess.check_output(["git", *args])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-code-map", action="store_true")
    args = parser.parse_args()
    entries = [p.decode("utf-8") for p in git("ls-files", "-z").split(b"\0") if p]
    if not entries:
        print("沒有可檢查的 Git 檔案，請先明確加入公開檔案。")
        return 1
    errors = []
    for name in entries:
        errors.extend(inspect_file(name, git("show", ":" + name)))
    if args.require_code_map:
        changed = git("diff", "--cached", "--name-only", "--no-renames", "-z").decode().split("\0")
        errors.extend(check_sync(changed))
    for error in errors:
        print(error)
    print(f"Public release check: {len(entries)} files, {len(errors)} errors. 常見金鑰檢查不取代人工檢閱。")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
