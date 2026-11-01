"""Small local launcher. Business operations stay in the API/core."""

import argparse
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
import webbrowser
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import httpx
from .security import Vault


def call_local(workspace, port, path, method="GET"):
    master = Vault(workspace).get("local_master")
    if not master:
        raise RuntimeError("服务尚未初始化，请先启动服务")
    with httpx.Client(
        base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5
    ) as client:
        challenge = secrets.token_hex(24)
        response = client.get("/api/health", params={"challenge": challenge})
        response.raise_for_status()
        health = response.json()
        proof = hmac.new(
            master.encode(), challenge.encode(), hashlib.sha256
        ).hexdigest()
        if health.get("app") != "api-post-local" or not hmac.compare_digest(
            proof, health.get("proof", "")
        ):
            raise RuntimeError("端口上的服务不属于此工作区")
        response = client.request(
            method, path, headers={"Authorization": "Bearer " + master}
        )
        response.raise_for_status()
        return response.json()


def main():
    parser = argparse.ArgumentParser(description="ReqDock 本地请求台")
    parser.add_argument("command", choices=["serve", "pair", "status", "open", "stop"])
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "workspace",
    )
    args = parser.parse_args()
    root = args.workspace.resolve()
    if not 1024 <= args.port <= 65535:
        parser.error("端口应在 1024–65535 之间")
    if args.command == "serve":
        import msvcrt
        import uvicorn
        from .app import create_app

        root.mkdir(parents=True, exist_ok=True)
        account = os.environ.get("USERDOMAIN", "") + "\\" + os.environ["USERNAME"]
        subprocess.run(
            [str(Path(os.environ["SystemRoot"]) / "System32" / "icacls.exe"), str(root), "/inheritance:r", "/grant:r", account + ":(OI)(CI)F"],
            check=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=30,
        )
        runtime = root / "runtime"
        runtime.mkdir(exist_ok=True)
        logging.basicConfig(
            level=logging.WARNING,
            handlers=[
                RotatingFileHandler(
                    runtime / "service.log",
                    maxBytes=5 * 1024 * 1024,
                    backupCount=2,
                    encoding="utf-8",
                )
            ],
        )
        lock = (runtime / "service.lock").open("a+b")
        lock.seek(0)
        lock.write(b"1")
        lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            lock.close()
            call_local(root, args.port, "/api/auth/session")
            return
        try:
            app = create_app(root, args.port)
            server = uvicorn.Server(
                uvicorn.Config(
                    app,
                    host="127.0.0.1",
                    port=args.port,
                    workers=1,
                    access_log=False,
                    log_level="warning",
                    timeout_graceful_shutdown=10,
                    limit_concurrency=32,
                    log_config=None,
                )
            )
            app.state.request_shutdown = lambda: setattr(server, "should_exit", True)
            server.run()
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            lock.close()
    elif args.command == "pair":
        access = call_local(root, args.port, "/api/auth/session")
        if access.get("access_mode") == "local":
            print(f"本机模式无需配对，直接打开 http://127.0.0.1:{args.port}/")
        else:
            data = call_local(root, args.port, "/api/auth/code", "POST")
            print(data["code"])
            print("配对码 5 分钟内有效，仅用于当前浏览器配对。", file=sys.stderr)
    elif args.command == "open":
        call_local(root, args.port, "/api/auth/session")
        webbrowser.open(f"http://127.0.0.1:{args.port}")
    elif args.command == "stop":
        print(json.dumps(call_local(root, args.port, "/api/service/stop", "POST")))
    else:
        print(
            json.dumps(
                call_local(root, args.port, "/api/auth/session"), ensure_ascii=False
            )
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = (
            "本地服务操作失败："
            + type(exc).__name__
            + "；请检查服务状态、端口与系统凭证库。"
        )
        logging.error(message)
        if sys.stderr is not None:
            print(message, file=sys.stderr)
        sys.exit(1)
