"""公開手冊的命令與 JSON 應符合目前程式，不需模型或網路。"""
import json
from pathlib import Path
import re
import shlex
import os
import subprocess
import shutil
import sys
import pytest

from app.studio.cli import _parser
from app.studio.contracts import JobRequest, WorkflowRequest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name != 'nt', reason='Windows 批次啟動器')
def test_named_launcher_preserves_project_root_and_serve_arguments(tmp_path):
    launcher = ROOT / 'Start-DonBee-Subtitle-Studio.bat'
    assert launcher.is_file()
    assert not (ROOT / 'run_v2.bat').exists()
    # 以替身直譯器攔截，不啟動服務、不安裝依賴或載入使用者模型。
    project = tmp_path / 'project with spaces'
    package = project / 'src/app'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('', encoding='utf-8')
    capture = tmp_path / 'launch.json'
    (package / '__main__.py').write_text(
        'import json, os, sys\nfrom pathlib import Path\n'
        'Path(os.environ["LAUNCH_CAPTURE"]).write_text(json.dumps([os.getcwd(), sys.argv[1:], os.environ["PYTHONPATH"]]), encoding="utf-8")\n',
        encoding='utf-8')
    copied = project / launcher.name
    shutil.copyfile(launcher, copied)
    env = dict(os.environ, STUDIO_MODEL_PYTHON=sys.executable, LAUNCH_CAPTURE=str(capture))
    subprocess.run(['cmd.exe', '/d', '/c', str(copied)], cwd=tmp_path, env=env, check=True, timeout=15)
    cwd, argv, pythonpath = json.loads(capture.read_text(encoding='utf-8'))
    assert Path(cwd) == project
    assert argv == ['serve', '--host', '127.0.0.1', '--port', '8765', '--open-browser']
    assert Path(pythonpath) == project / 'src'


@pytest.mark.skipif(os.name != 'nt', reason='Windows 批次啟動器')
def test_launcher_default_enters_setup_and_forwards_confirmed_choices(tmp_path):
    project = tmp_path / 'first run with spaces'
    package = project / 'bootstrap'
    package.mkdir(parents=True)
    capture = tmp_path / 'setup.json'
    (package / '__init__.py').write_text('', encoding='utf-8')
    (package / '__main__.py').write_text(
        'import os, sys, json\nfrom pathlib import Path\n'
        'Path(os.environ["LAUNCH_CAPTURE"]).write_text(json.dumps([os.getcwd(), sys.argv[1:]]), encoding="utf-8")\n', encoding='utf-8')
    copied = project / 'Start-DonBee-Subtitle-Studio.bat'
    shutil.copyfile(ROOT / copied.name, copied)
    env = dict(os.environ, LAUNCH_CAPTURE=str(capture), PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'])
    env.pop('STUDIO_MODEL_PYTHON', None)
    subprocess.run(['cmd.exe', '/d', '/c', str(copied), '--local', '--confirm'], cwd=tmp_path, env=env, check=True, timeout=15)
    cwd, argv = json.loads(capture.read_text(encoding='utf-8'))
    assert Path(cwd) == project
    assert argv == ['--interactive', '--serve', '--local', '--confirm']


def test_manual_cli_examples_parse():
    text = (ROOT / "guides/API_CLI.md").read_text(encoding="utf-8")
    commands = re.findall(r"^& \$py -m app (.+)$", text, re.M)
    assert len(commands) >= 15
    for command in commands:
        _parser().parse_args(shlex.split(command))


def test_manual_job_and_plan_examples_validate():
    text = (ROOT / "guides/API_CLI.md").read_text(encoding="utf-8")
    blocks = [json.loads(value) for value in re.findall(r"```json\n(.*?)\n```", text, re.S)]
    assert len(blocks) == 3
    JobRequest.model_validate(blocks[0])
    WorkflowRequest.model_validate(blocks[2])


def test_public_document_links_resolve_without_private_docs():
    files = [ROOT / "README.md", ROOT / "README.en.md", ROOT / "AGENTS.md", ROOT / "THIRD_PARTY_NOTICES.md",
             ROOT / ".agents/skills/subtitle-studio/SKILL.md", *sorted((ROOT / "guides").glob("*.md"))]
    for path in files:
        for link in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if "://" in link or link.startswith("#"):
                continue
            target = (path.parent / link.split("#")[0]).resolve()
            assert target.exists(), (path.name, link)
            assert not target.is_relative_to(ROOT / "docs"), (path.name, link)
