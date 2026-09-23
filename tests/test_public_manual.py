"""公開手冊的命令與 JSON 應符合目前程式，不需模型或網路。"""
import json
from pathlib import Path
import re
import shlex

from app.studio.cli import _parser
from app.studio.contracts import JobRequest, WorkflowRequest

ROOT = Path(__file__).resolve().parents[1]


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
