"""Repeatable native Windows comparison using only loopback API/MySQL fixtures.

No user workspace is read. Measurements include the native client, local service
and all WebView2 descendants, excluding this fixture/coordinator process.
"""

import argparse
import hashlib
import json
import os
import socket
import statistics
import subprocess
import sys
import threading
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import httpx
import psutil


class Payload(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(min(size, 1024**2))
        payload = json.dumps({"content": "memory-payload-" + "x" * 260000}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18777)
    parser.add_argument("--idle-seconds", type=int, default=80)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or not 75 <= args.idle_seconds <= 300:
        parser.error("Port / idle interval out of bounds")
    project = Path(__file__).resolve().parents[1]
    source = args.source.resolve()
    root = project / "workspace/qa" / ("memory-bench-" + uuid4().hex)
    root.mkdir(parents=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", args.port))
    sys.path.insert(0, str(project / "tests"))
    from mysql_fixture import MySQLFixture

    mysql = MySQLFixture()
    provider = ThreadingHTTPServer(("127.0.0.1", 0), Payload)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(source / "src")
    env["PYTHONUTF8"] = "1"
    log = (root / "desktop.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            str(Path(sys.executable).with_name("pythonw.exe")),
            str(project / "scripts/memory_desktop_probe.py"),
            "--source",
            str(source),
            "--workspace",
            str(root),
            "--port",
            str(args.port),
        ],
        cwd=source,
        env=env,
        stdout=log,
        stderr=log,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    def wait(fn, seconds=40):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if process.poll() is not None:
                raise RuntimeError("QA desktop exited; inspect its private log")
            value = fn()
            if value:
                return value
            time.sleep(0.1)
        raise RuntimeError("QA condition timed out")

    def read(path):
        try:
            return json.loads(path.read_text("utf-8"))
        except (FileNotFoundError, ValueError):
            return None

    def command(name):
        seq = uuid4().hex
        path = root / "probe-command.json"
        tmp = root / "probe-command.tmp"
        tmp.write_text(json.dumps({"seq": seq, "command": name}), encoding="utf-8")
        tmp.replace(path)
        value = wait(
            lambda: (
                v
                if (v := read(root / "probe-result.json")) and v.get("seq") == seq
                else None
            ),
            15,
        )
        if "error" in value:
            raise RuntimeError("Probe " + name + " failed: " + value["error"])
        return value["result"]

    def measure(count=5):
        samples = []
        for _ in range(count):
            groups = defaultdict(
                lambda: {"uss": 0, "commit": 0, "rss": 0, "processes": 0}
            )
            parent = psutil.Process(process.pid)
            for p in [parent, *parent.children(recursive=True)]:
                try:
                    m = p.memory_full_info()
                    name = p.name().lower()
                    argv = p.cmdline()
                    kind = (
                        "webview"
                        if "webview" in name
                        else "service"
                        if "serve" in argv
                        else "desktop"
                    )
                    g = groups[kind]
                    g["uss"] += m.uss
                    g["commit"] += getattr(m, "private", m.vms)
                    g["rss"] += m.rss
                    g["processes"] += 1
                except psutil.Error:
                    pass
            row = {
                kind: {
                    k: round(v / 1024**2, 2) if k != "processes" else v
                    for k, v in value.items()
                }
                for kind, value in groups.items()
            }
            row["total"] = {
                k: round(sum(v[k] for v in groups.values()) / 1024**2, 2)
                for k in ["uss", "commit", "rss"]
            }
            samples.append(row)
            time.sleep(1)
        return {
            "median": {
                kind: {
                    k: round(
                        statistics.median(s.get(kind, {}).get(k, 0) for s in samples), 2
                    )
                    for k in ["uss", "commit", "rss"]
                }
                for kind in ["desktop", "service", "webview", "total"]
            },
            "samples": samples,
        }

    try:
        ready = wait(lambda: read(root / "probe-ready.json"))
        print(
            json.dumps(
                {
                    "phase": "ready",
                    "label": args.label,
                    "policy": ready["policy_available"],
                }
            ),
            flush=True,
        )
        with httpx.Client(
            base_url="http://127.0.0.1:" + str(args.port), trust_env=False, timeout=15
        ) as client:

            def api(method, path, **kwargs):
                r = client.request(method, "/api" + path, **kwargs)
                r.raise_for_status()
                return r.json()

            env_record = api(
                "POST",
                "/environments",
                json={
                    "name": "memory fixture",
                    "base_url": "http://127.0.0.1:" + str(provider.server_port),
                    "network_scope": "local",
                },
            )
            profile = api(
                "POST",
                "/sql/connections",
                json={
                    "name": "memory SQL fixture",
                    "environment": "isolated",
                    "host": "127.0.0.1",
                    "port": mysql.port,
                    "username": "fixture",
                    "password": "fixture-password",
                    "database": "lab",
                    "network_scope": "local",
                    "tls": False,
                    "allow_write": False,
                    "trusted": True,
                },
            )

            def finish(record, path):
                end = time.monotonic() + 20
                while (
                    record["state"] in ["queued", "running"] and time.monotonic() < end
                ):
                    time.sleep(0.03)
                    record = api("GET", path + record["id"])
                assert record["state"] == "completed"
                return record

            for n in range(20):
                r = api(
                    "POST",
                    "/runs",
                    json={
                        "environment_id": env_record["id"],
                        "submission_key": str(uuid4()),
                        "name": "Memory fixture " + str(n),
                        "path": "/payload",
                        "body": "{}",
                        "execution_context": {
                            "source": "skill",
                            "agent_name": "MemoryBench",
                            "task_id": "memory-fixture",
                        },
                    },
                )
                finish(r, "/runs/")
            command("history")
            wait(lambda: command("page").get("rows") == 20, 15)
            command("activate")
            time.sleep(3)
            normal = measure()
            print(
                json.dumps(
                    {
                        "phase": "visible",
                        "label": args.label,
                        "median": normal["median"]["total"],
                    }
                ),
                flush=True,
            )
            command("minimize")
            started = time.monotonic()
            agent_records = []
            while time.monotonic() - started < args.idle_seconds:
                elapsed = time.monotonic() - started
                if elapsed >= 12 and not agent_records:
                    groups = api("GET", "/environment-groups")
                    g, m = next(
                        (g, m)
                        for g in groups
                        for m in g["members"]
                        if m["environment"]["id"] == env_record["id"]
                    )
                    for agent, folder in [
                        ("Codex", ".agents"),
                        ("ClaudeCode", ".claude"),
                    ]:
                        entry = (
                            Path.home() / folder / "skills/reqdock/scripts/reqdock.py"
                        )
                        sql = root / "fixture.sql"
                        sql.write_text(
                            "SELECT id,name FROM records WHERE id<3", encoding="utf-8"
                        )

                        def skill(arguments, entry=entry):
                            p = subprocess.run(
                                [
                                    str(Path(sys.base_prefix) / "python.exe"),
                                    str(entry),
                                    "--workspace",
                                    str(root),
                                    "--port",
                                    str(args.port),
                                    *arguments,
                                ],
                                check=False,
                                capture_output=True,
                                encoding="utf-8",
                                timeout=25,
                                cwd=root,
                            )
                            if p.returncode:
                                raise RuntimeError(
                                    "Installed Skill failed during minimized benchmark"
                                )
                            return json.loads(p.stdout)

                        request = root / ("api-" + agent + ".json")
                        request.write_text(
                            json.dumps(
                                {
                                    "name": "Idle Skill fixture",
                                    "path": "/payload",
                                    "body": "{}",
                                }
                            ),
                            encoding="utf-8",
                        )
                        a = skill(
                            [
                                "run",
                                "--request-file",
                                str(request),
                                "--environment",
                                g["name"],
                                "--platform",
                                m["variable"],
                                "--task-id",
                                "memory-idle-skill",
                                "--wait",
                                "10",
                            ]
                        )
                        q = skill(
                            [
                                "sql-run",
                                "--connection",
                                profile["id"],
                                "--database",
                                "lab",
                                "--sql-file",
                                str(sql),
                                "--task-id",
                                "memory-idle-skill",
                                "--wait",
                                "10",
                            ]
                        )
                        assert (
                            a["state"] == q["state"] == "completed"
                            and a["status_code"] == 200
                            and len(q["rows"]) == 2
                        )
                        agent_records.extend([a["id"], q["id"]])
                    print(
                        json.dumps(
                            {
                                "phase": "minimized_skills_passed",
                                "label": args.label,
                                "records": len(agent_records),
                            }
                        ),
                        flush=True,
                    )
                time.sleep(1)
            idle = measure()
            pools = api("GET", "/sql/pools")
            assert not pools
            native_state = command("state")
            print(
                json.dumps(
                    {
                        "phase": "minimized_idle",
                        "label": args.label,
                        "median": idle["median"]["total"],
                        "native_state": native_state,
                        "sql_pools": len(pools),
                    }
                ),
                flush=True,
            )
            command("restore")
            time.sleep(3)
            restored = measure()
            assert command("page")["rows"] == 20
            restored_native = command("state")
            assert (
                restored_native["target"] == "Normal"
                and not restored_native["minimized"]
            )
            command("open_record")
            wait(lambda: command("response").get("body"), 15)
            projection_bytes = len(
                client.get("/api/runs?limit=20&compact=true").content
            )
            report = {
                "source_worktree_dirty": bool(
                    subprocess.check_output(
                        ["git", "status", "--porcelain"], cwd=source
                    ).strip()
                ),
                "source_fingerprints": {
                    name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                    for name in [
                        "src/api_post/desktop.py",
                        "src/api_post/storage.py",
                        "web/src/main.tsx",
                    ]
                },
                "history_list_response_bytes": projection_bytes,
                "restored_native_state": restored_native,
                "label": args.label,
                "source": str(source),
                "source_commit": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=source
                )
                .decode()
                .strip(),
                "fixture_api_responses": 22,
                "payload_bytes": 260030,
                "idle_seconds": args.idle_seconds,
                "policy_available": ready["policy_available"],
                "visible": normal,
                "minimized_idle": idle,
                "restored": restored,
                "minimized_native_state": native_state,
                "sql_pools_after_idle": len(pools),
                "installed_skill_operations_passed": len(agent_records),
                "record_detail_after_restore": True,
                "workspace": str(root),
                "measurement": "MiB; USS=private resident, commit=private committed, RSS includes shared pages",
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(
                json.dumps({"report": str(args.output), "complete": True}), flush=True
            )
    finally:
        try:
            command("close")
        except (RuntimeError, OSError) as exc:
            print(
                json.dumps({"phase": "probe_cleanup", "error": type(exc).__name__}),
                flush=True,
            )
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            for child in psutil.Process(process.pid).children(recursive=True):
                child.terminate()
            process.terminate()
        log.close()
        provider.shutdown()
        provider.server_close()
        thread.join(2)
        mysql.close()


if __name__ == "__main__":
    main()
