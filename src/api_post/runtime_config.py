"""Optional per-user workspace selection; contains paths only, never credentials."""

import json
import os
from pathlib import Path


def workspace_for(project):
    project = Path(project).resolve()
    fallback = project / "workspace"
    configured = os.environ.get("REQDOCK_RUNTIME_CONFIG")
    local = os.environ.get("LOCALAPPDATA")
    if not configured and not local:
        return fallback
    path = Path(configured) if configured else Path(local) / "ReqDock" / "runtime.json"
    if not path.exists():
        return fallback
    if path.stat().st_size > 8192:
        raise ValueError("ReqDock runtime configuration exceeds 8 KiB")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict) or set(value) - {
        "schema_version",
        "project_root",
        "workspace",
    }:
        raise ValueError("Invalid ReqDock runtime configuration")
    configured_project, workspace = value.get("project_root"), value.get("workspace")
    if not isinstance(configured_project, str) or not isinstance(workspace, str):
        raise ValueError("ReqDock runtime paths must be strings")
    if not Path(configured_project).is_absolute() or not Path(workspace).is_absolute():
        raise ValueError("ReqDock runtime paths must be absolute")
    if Path(configured_project).resolve() != project:
        return fallback  # Another checkout must not silently adopt this workspace.
    return Path(workspace).resolve()
