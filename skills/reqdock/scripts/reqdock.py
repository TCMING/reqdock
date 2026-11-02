"""Portable installed Skill entry point; no third-party imports or secrets here."""

import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4


def command(arguments, config_path=None):
    path = (
        Path(config_path)
        if config_path
        else Path(__file__).resolve().parents[1] / "config.json"
    )
    if not path.is_file() or path.stat().st_size > 8192:
        raise ValueError(
            "ReqDock Skill is not configured; run scripts/install-skills.ps1 first"
        )
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict) or set(value) != {"project_root", "agent_name"}:
        raise ValueError("Invalid ReqDock Skill configuration")
    if (
        not isinstance(value["project_root"], str)
        or not Path(value["project_root"]).is_absolute()
    ):
        raise ValueError("ReqDock project root must be an absolute path")
    root = Path(value["project_root"]).resolve()
    python = root / ".venv" / "Scripts" / "python.exe"
    if not python.is_file() or not (root / "src/api_post/skill_cli.py").is_file():
        raise ValueError("ReqDock runtime is missing at the configured project root")
    agent = value["agent_name"]
    if agent not in {"Codex", "ClaudeCode"}:
        raise ValueError("Invalid installed Skill Agent name")
    args = list(arguments)
    # Determine the command after optional global options, not by scanning values.
    at = 0
    while at < len(args):
        if args[at] in {"--workspace", "--port"}:
            at += 2
        elif args[at].startswith(("--workspace=", "--port=")):
            at += 1
        else:
            break
    action = args[at] if at < len(args) else ""
    if action in {"run", "sql-run"} and not any(
        x in args[at + 1 :] for x in {"--help", "-h"}
    ):
        for option, default in [
            ("--source", "skill"),
            ("--agent", agent),
            ("--task-id", "reqdock-" + str(uuid4())),
        ]:
            if not any(
                x == option or x.startswith(option + "=") for x in args[at + 1 :]
            ):
                args.extend([option, default])
    return root, [str(python), "-m", "api_post.skill_cli", *args]


def main():
    try:
        root, args = command(sys.argv[1:])
        return subprocess.run(
            args, cwd=root, shell=False, env=os.environ.copy()
        ).returncode
    except (ValueError, OSError):
        print(
            json.dumps(
                {
                    "error": "ReqDock Skill entry failed; check installation paths and runtime configuration."
                }
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
