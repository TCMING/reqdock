import importlib.util
import json
from pathlib import Path
import pytest
from api_post.runtime_config import workspace_for


def runtime(tmp_path, monkeypatch, project=None):
    project = project or tmp_path / "project"
    project.mkdir(exist_ok=True)
    config = tmp_path / "runtime.json"
    monkeypatch.setenv("REQDOCK_RUNTIME_CONFIG", str(config))
    return project, config


def test_workspace_selection_preserves_path_without_copy(tmp_path, monkeypatch):
    project, config = runtime(tmp_path, monkeypatch)
    workspace = tmp_path / "original-workspace"
    workspace.mkdir()
    sentinel = workspace / "original.json"
    sentinel.write_text("unchanged", encoding="utf-8")
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_root": str(project),
                "workspace": str(workspace),
            }
        ),
        encoding="utf-8",
    )
    assert workspace_for(project) == workspace.resolve()
    assert sentinel.read_text("utf-8") == "unchanged"
    other = tmp_path / "other"
    other.mkdir()
    assert workspace_for(other) == other / "workspace"


def test_workspace_config_is_bounded_and_strict(tmp_path, monkeypatch):
    project, config = runtime(tmp_path, monkeypatch)
    assert workspace_for(project) == project / "workspace"
    for value in [
        {"project_root": str(project), "workspace": "relative"},
        {
            "project_root": str(project),
            "workspace": str(tmp_path),
            "token": "forbidden-field",
        },
        [],
    ]:
        config.write_text(json.dumps(value), encoding="utf-8")
        with pytest.raises(ValueError):
            workspace_for(project)
    config.write_text(" " * 8193, encoding="utf-8")
    with pytest.raises(ValueError):
        workspace_for(project)


@pytest.fixture
def launcher(tmp_path):
    path = Path(__file__).resolve().parents[1] / "skills/reqdock/scripts/reqdock.py"
    spec = importlib.util.spec_from_file_location("reqdock_skill_launcher", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "installed-app"
    (root / ".venv/Scripts").mkdir(parents=True)
    (root / ".venv/Scripts/python.exe").touch()
    (root / "src/api_post").mkdir(parents=True)
    (root / "src/api_post/skill_cli.py").touch()
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"project_root": str(root), "agent_name": "ClaudeCode"}),
        encoding="utf-8",
    )
    return module, root, config


def test_launcher_tags_each_client_and_task_without_overwriting(
    launcher, monkeypatch, tmp_path
):
    module, root, config = launcher
    monkeypatch.chdir(tmp_path)
    cwd, args = module.command(["sql-run", "--sql-file", "query.sql"], config)
    assert cwd == root and args[:3] == [
        str(root / ".venv/Scripts/python.exe"),
        "-m",
        "api_post.skill_cli",
    ]
    assert (
        args[args.index("--source") + 1] == "skill"
        and args[args.index("--agent") + 1] == "ClaudeCode"
    )
    assert args[args.index("--task-id") + 1].startswith("reqdock-")
    _, custom = module.command(
        [
            "--workspace=" + str(tmp_path),
            "--port=18767",
            "run",
            "--agent",
            "Custom",
            "--task-id=shared",
        ],
        config,
    )
    assert custom.count("--agent") == 1 and "--task-id" not in custom
    assert custom[custom.index("--agent") + 1] == "Custom" and "--source" in custom
    _, query = module.command(["records", "--task-id", "shared"], config)
    assert "--source" not in query and "--agent" not in query


def test_launcher_rejects_unconfigured_roots(launcher):
    module, root, config = launcher
    config.write_text(
        json.dumps({"project_root": str(root), "agent_name": "unrecognized"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        module.command(["status"], config)
    config.unlink()
    with pytest.raises(ValueError):
        module.command(["status"], config)
