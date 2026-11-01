"""Disposable local fixtures. Never opens the user's normal workspace or vendors."""

import argparse
from pathlib import Path
import threading
from http.server import ThreadingHTTPServer
import uvicorn
from api_post.app import create_app
from api_post.demo import Handler
from api_post.openapi_import import Importer
from api_post.security import Vault


class PreviewHandler(Handler):
    def do_GET(self):
        if self.path == "/preview-fixture.png":
            import base64
            payload = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lS8AAAAASUVORK5CYII=")
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        super().do_GET()


class TestVault(Vault):
    def __init__(self, root):
        super().__init__(root)
        self.created = set()

    def set(self, key, value):
        super().set(key, value)
        self.created.add(key)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    root = args.workspace.resolve()
    allowed = (project / "workspace/qa").resolve()
    if (
        not root.is_relative_to(allowed)
        or not root.name.startswith("e2e-")
        or root.exists()
    ):
        raise RuntimeError("A new isolated QA workspace is required")
    import sys
    sys.path.insert(0, str(project / "tests"))
    from mysql_fixture import MySQLFixture
    sql_fixture = MySQLFixture(18769)
    # Only disposable QA workspaces may opt into the local no-pairing flow.
    import os,json
    if os.environ.get("API_LAB_E2E_ACCESS_MODE") == "local":
        root.mkdir(parents=True)
        (root / "access.json").write_text(json.dumps({"schema_version":1,"mode":"local"}),encoding="utf-8")
    vault = TestVault(root)
    app = create_app(root, 18767, vault=vault)
    base = "http://127.0.0.1:18768"
    definitions = [
        ("本地环境", ["aibots-common", "aibots-intl", "ibrain-common", "idle-common"]),
        (
            "线上环境",
            [
                "ibrain-common",
                "idle-common",
                "aibots-common",
                "aibots-intl",
                "aibots-skywalker",
                "alert-url",
            ],
        ),
        ("测试环境", ["aibots-common", "ibrain-common"]),
        ("灰度环境", ["aibots-common", "ibrain-common"]),
        ("海外线上环境", ["aibots-intl", "ibrain-common"]),
        ("海外测试环境", ["ibrain-common"]),
        ("海外灰度环境", ["ibrain-common"]),
    ]
    servers = [
        {
            "description": name,
            "url": "",
            "variables": {
                key: {"default": base + "/" + str(i) + "/" + key} for key in keys
            },
        }
        for i, (name, keys) in enumerate(definitions)
    ]
    paths = {
        "{{ibrain-common}}/fixture/" + str(i): {
            "post": {
                "summary": ("gemini fixture " if i == 0 else "Fixture operation ")
                + str(i),
                "tags": [
                    "ibrain接口测试/Prompt工程/生图片/banana"
                    if i == 0
                    else "本机测试用例"
                ],
                "requestBody": {
                    "content": {
                        "application/json": {"example": {"message": "local fixture"}}
                    }
                },
            }
        }
        for i in range(146)
    }
    Importer(app.state.store, vault, 18767).run(
        {
            "openapi": "3.0.3",
            "info": {"title": "AI服务平台", "version": "fixture"},
            "servers": servers,
            "paths": paths,
        },
        "apipost-ai-services",
    )
    app.state.store.save_env(
        {
            "name": "本机演示（隔离测试）",
            "base_url": base,
            "region": "local",
            "stage": "local",
            "network_scope": "local",
            "credential_ref": None,
            "variables": {},
            "read_timeout": 120,
            "total_timeout": 600,
        }
    )
    upstream = ThreadingHTTPServer(("127.0.0.1", 18768), PreviewHandler)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=18767,
            access_log=False,
            log_level="warning",
            timeout_graceful_shutdown=10,
        )
    )
    app.state.request_shutdown = lambda: setattr(server, "should_exit", True)
    try:
        server.run()
    finally:
        sql_fixture.close()
        upstream.shutdown()
        upstream.server_close()
        thread.join(2)
        for key in vault.created:
            vault.delete(key)
        (root / "closed.json").write_text("{}", encoding="utf-8")


if __name__ == "__main__":
    main()
