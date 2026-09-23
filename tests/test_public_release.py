"""公開版本不得攜帶本機資料；檢查以 Git 內容為準，不能只信忽略規則。"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def checker():
    spec = importlib.util.spec_from_file_location("release_check", ROOT / "scripts/check-public-release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reject_private_files_even_when_force_added():
    check = checker()
    for name in ["docs/HANDOFF.md", "docs/CONVERSATION_LOG.md", "data/secrets.json",
                 "uploads/a.mp4", "models/hf/a.bin", ".env", "frontend/TEST_REPORT.md",
                 "scripts/smoke-draft-offline.py", ".claude/settings.local.json"]:
        assert check.inspect_file(name, b"ordinary text"), name


def test_allow_runtime_and_public_instructions():
    check = checker()
    for name in ["Start-DonBee-Subtitle-Studio.bat", "src/app/studio/api.py", "bootstrap/run.py", "frontend/dist/assets/index.js",
                 "README.md", "guides/API_CLI.md", ".agents/skills/subtitle-studio/SKILL.md",
                 "src/app/vendor/vibevoice/LICENSE", "tests/test_public_release.py"]:
        assert not check.inspect_file(name, b"ordinary text"), name


def test_detect_secret_without_echoing_value():
    secret = "sk-or-v1-" + "a" * 64
    errors = checker().inspect_file("src/app/example.py", secret.encode())
    assert errors and all(secret not in error for error in errors)


def test_reject_media_and_large_blobs():
    assert checker().inspect_file("src/video.mp4", b"video")
    assert checker().inspect_file("frontend/tests/result.vtt", b"WEBVTT")
    assert checker().inspect_file("frontend/tests/result.ass", b"subtitle")
    assert checker().inspect_file("frontend/dist/huge.js", b"x" * (10 * 1024 * 1024 + 1))


def test_code_map_required_when_code_changes():
    check = checker()
    assert check.check_sync(["src/app/studio/api.py"])
    assert not check.check_sync(["src/app/studio/api.py", "guides/CODE_MAP.md"])
    assert not check.check_sync(["README.md"])


def test_private_paths_are_ignored():
    import subprocess
    for name in ["docs/HANDOFF.md", "downloads/sample.mp4", ".venv-qwen-asr/pyvenv.cfg",
                 "data/config.json", "frontend/TEST_REPORT.md"]:
        result = subprocess.run(["git", "check-ignore", "--quiet", name], cwd=ROOT)
        assert result.returncode == 0, name


def test_prebuilt_web_entry_is_not_ignored():
    import subprocess
    result = subprocess.run(["git", "check-ignore", "--quiet", "frontend/dist/index.html"], cwd=ROOT)
    assert result.returncode == 1, "公開包必須包含可直接使用的網頁程式打包"


def test_force_added_private_file_blocks_real_git_index(tmp_path):
    import subprocess
    import sys
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "HANDOFF.md").write_text("private history", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "docs/HANDOFF.md"], check=True)
    result = subprocess.run([sys.executable, str(ROOT / "scripts/check-public-release.py")], cwd=tmp_path, capture_output=True)
    assert result.returncode == 1
    assert b"docs/HANDOFF.md" in result.stdout
