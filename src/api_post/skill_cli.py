"""API-only entry point shared by the user-scoped Codex skill and local UI."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4
import httpx
from .__main__ import call_local
from .security import Vault


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="ReqDock: shared cases, environments and run records"
    )
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--port", type=int, default=18765)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("start")
    sub.add_parser("status")
    sub.add_parser("environments").add_argument("--name", default="")
    for command in ["cases", "resources"]:
        p = sub.add_parser(command)
        p.add_argument("--search", default="")
        p.add_argument("--id")
        if command == "cases":
            p.add_argument("--module", default="")
            p.add_argument("--group", default="")
    p = sub.add_parser("runs")
    p.add_argument("--id")
    p.add_argument("--page", type=int, default=1)
    p = sub.add_parser("events")
    p.add_argument("id")
    p.add_argument("--offset", type=int, default=0)
    p = sub.add_parser("cancel")
    p.add_argument("id")
    p = sub.add_parser("run")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--case")
    source.add_argument("--request-file", type=Path)
    p.add_argument("--environment", required=True)
    p.add_argument("--platform", required=True)
    p.add_argument("--wait", type=float, default=0)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    workspace = (args.workspace or root / "workspace").resolve()
    if not 1024 <= args.port <= 65535:
        parser.error("Invalid local port")
    if args.command == "start":
        if args.workspace or args.port != 18765:
            parser.error("Desktop start uses the normal workspace and port")
        subprocess.Popen(
            [str(root / "ReqDock.exe")],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        print(
            json.dumps(
                {"started": True, "mode": "desktop", "url": "http://127.0.0.1:18765"}
            )
        )
        return
    # Challenge verification happens before sending a credential to the listening process.
    call_local(workspace, args.port, "/api/auth/session")
    master = Vault(workspace).get("local_master")
    with httpx.Client(
        base_url=f"http://127.0.0.1:{args.port}",
        headers={"Authorization": "Bearer " + master},
        trust_env=False,
        follow_redirects=False,
        timeout=30,
    ) as client:

        def api(method, path, **kwargs):
            r = client.request(method, "/api" + path, **kwargs)
            if not r.is_success:
                raise RuntimeError(
                    "Local API HTTP "
                    + str(r.status_code)
                    + ": "
                    + str(r.json().get("detail", "操作失败"))[:256]
                )
            return r.json()

        def id(value):
            from uuid import UUID

            return str(UUID(value))

        if args.command in {"status", "environments"}:
            groups = api("GET", "/environment-groups")
            if args.command == "status":
                result = {
                    "ready": True,
                    "workspace": str(workspace),
                    "groups": len(groups),
                }
            else:
                result = [
                    {
                        "id": g["id"],
                        "name": g["name"],
                        "revision": g["revision"],
                        "platforms": [
                            {
                                "name": m["variable"],
                                "environment_id": m["environment"]["id"],
                                "base_url": m["environment"]["base_url"],
                            }
                            for m in g["members"]
                        ],
                    }
                    for g in groups
                    if args.name.lower() in g["name"].lower()
                ]
        elif args.command in {"cases", "resources"}:
            result = api(
                "GET",
                "/" + args.command + ("/" + id(args.id) if args.id else ""),
                params={}
                if args.id
                else {
                    "search": args.search,
                    "limit": 30,
                    **(
                        {"module": args.module, "group": args.group}
                        if args.command == "cases"
                        else {}
                    ),
                },
            )
        elif args.command == "runs":
            result = api(
                "GET",
                "/runs" + ("/" + id(args.id) if args.id else ""),
                params={} if args.id else {"page": args.page, "limit": 20},
            )
        elif args.command == "events":
            result = api(
                "GET",
                "/runs/" + id(args.id) + "/events",
                params={"offset": args.offset},
            )
        elif args.command == "cancel":
            result = api("POST", "/runs/" + id(args.id) + "/cancel")
        else:
            if not 0 <= args.wait <= 60:
                parser.error("--wait must be between 0 and 60 seconds")
            groups = [
                g
                for g in api("GET", "/environment-groups")
                if g["name"] == args.environment
            ]
            if len(groups) != 1:
                raise ValueError("环境名称未找到或不唯一，请先查询环境列表")
            members = [
                m for m in groups[0]["members"] if m["variable"] == args.platform
            ]
            if len(members) != 1:
                raise ValueError("服务变量/平台名未找到或不唯一，请先查询环境列表")
            if args.case:
                case = api("GET", "/cases/" + id(args.case))
                request = {**case["request"], "case_id": case["id"]}
            else:
                path = args.request_file.resolve()
                if not path.is_file() or path.stat().st_size > 8 * 1024**2:
                    raise ValueError("请求文件不存在或超过8 MiB")
                request = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(request, dict):
                    raise ValueError("请求文件必须是JSON对象")
            request.update(
                environment_id=members[0]["environment"]["id"],
                submission_key=str(uuid4()),
            )
            result = api("POST", "/runs", json=request)
            deadline = time.monotonic() + args.wait
            while (
                result["state"] in {"queued", "running"} and time.monotonic() < deadline
            ):
                time.sleep(0.5)
                result = api("GET", "/runs/" + result["id"])
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        if isinstance(exc, (ValueError, RuntimeError)):
            print(
                json.dumps({"error": str(exc)[:300]}, ensure_ascii=False),
                file=sys.stderr,
            )
        else:
            print(
                json.dumps(
                    {
                        "error": "本地工具操作失败："
                        + type(exc).__name__
                        + "。请先打开客户端并核对工作区、端口和系统凭证库。"
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
        sys.exit(1)
