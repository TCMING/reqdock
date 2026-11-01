import asyncio
import json
import socket
import threading
import time
from uuid import uuid4
from http.server import ThreadingHTTPServer
import pytest
from fastapi.testclient import TestClient
from api_post.app import create_app
from api_post.demo import Handler
from api_post.network import PolicyError, check_ip, PinnedBackend
from api_post.storage import Store


class MemoryVault:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        self.data[key] = value

    def delete(self, key):
        self.data.pop(key, None)


@pytest.fixture
def upstream():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:" + str(server.server_port)
    server.shutdown()
    server.server_close()
    thread.join(2)


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "data", vault=MemoryVault())
    with TestClient(
        app,
        base_url="http://127.0.0.1:18765",
        headers={"Authorization": "Bearer " + app.state.auth.master},
    ) as client:
        yield client


def environment(client, url, **extra):
    response = client.post(
        "/api/environments", json={"name": "本机验证", "base_url": url, **extra}
    )
    assert response.status_code == 200, response.text
    return response.json()


def submit(client, env, **extra):
    response = client.post(
        "/api/runs",
        json={
            "environment_id": env["id"],
            "submission_key": str(uuid4()),
            "name": "集成验证",
            "path": "/echo",
            "body": '{"message":"你好"}',
            "expected_status": 200,
            **extra,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def finish(client, id):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        result = client.get("/api/runs/" + id).json()
        if result["state"] not in {"queued", "running"}:
            return result
        time.sleep(0.03)
    raise AssertionError("运行未结束")


def test_json_wire_and_history(client, upstream):
    env = environment(client, upstream)
    result = finish(
        client, submit(client, env, headers={"X-Test-Header": "hello"})["id"]
    )
    assert result["state"] == "completed", result
    assert result["validation_result"] == "pass"
    body = json.loads(result["response_preview"])
    assert body["body"] == {"message": "你好"}
    assert body["headers"]["X-Test-Header"] == "hello"
    assert body["method"] == "POST"
    assert result["request"]["headers_source"] == "final_pre_send"
    assert result["request"]["headers"] == {
        k.lower(): v for k, v in body["headers"].items()
    }
    assert result["request"]["header_items"]
    assert client.get("/api/runs").json()["total"] == 1
    assert client.get("/api/runs?environment_id=" + str(uuid4())).json()["total"] == 0


def test_negative_case_and_no_redirect(client, upstream):
    env = environment(client, upstream)
    error = finish(
        client, submit(client, env, path="/error", expected_status=400)["id"]
    )
    assert error["state"] == "completed" and error["validation_result"] == "pass"
    redirect = finish(
        client, submit(client, env, path="/redirect", expected_status=302)["id"]
    )
    assert redirect["status_code"] == 302 and redirect["validation_result"] == "pass"


def test_sse_utf8_and_terminal_marker(client, upstream):
    env = environment(client, upstream)
    record = finish(
        client, submit(client, env, path="/sse", stream=True, contains="流式响应")["id"]
    )
    assert record["state"] == "completed", record
    assert record["validation_result"] == "pass", record
    assert record["first_content_ms"] >= 0
    events = client.get("/api/runs/" + record["id"] + "/events").json()
    assert len(events["items"]) == 5 and events["items"][-1]["data"] == "[DONE]"
    assert "你好" in events["items"][0]["data"]
    assert not client.get(
        "/api/runs/" + record["id"] + "/events?offset=" + str(events["offset"])
    ).json()["items"]


def test_environment_conflict_and_exact_target(client, upstream):
    env = environment(client, upstream)
    data = {
        k: v
        for k, v in env.items()
        if k
        in {
            "name",
            "base_url",
            "region",
            "stage",
            "network_scope",
            "credential_ref",
            "read_timeout",
            "total_timeout",
            "revision",
        }
    }
    assert (
        client.put(
            "/api/environments/" + env["id"], json={**data, "name": "新名称"}
        ).status_code
        == 200
    )
    assert client.put("/api/environments/" + env["id"], json=data).status_code == 400
    response = client.post(
        "/api/runs",
        json={
            "environment_id": env["id"],
            "submission_key": str(uuid4()),
            "path": "http://169.254.169.254/latest",
            "body": "{}",
        },
    )
    assert response.status_code == 400
    assert (
        client.post(
            "/api/environments",
            json={"name": "self", "base_url": "http://127.0.0.1:18765"},
        ).status_code
        == 400
    )


def test_timeout_and_queued_cancel(client, upstream):
    env = environment(client, upstream, total_timeout=1)
    slow = submit(client, env, path="/slow")
    queued = submit(client, env)
    assert (
        client.post("/api/runs/" + queued["id"] + "/cancel").json()["state"]
        == "cancelled"
    )
    assert finish(client, slow["id"])["state"] == "timed_out"


def test_idempotency(client, upstream):
    env = environment(client, upstream)
    key = str(uuid4())
    first = submit(client, env, submission_key=key)
    second = submit(client, env, submission_key=key)
    assert first["id"] == second["id"]
    assert client.get("/api/runs").json()["total"] == 1
    assert finish(client, first["id"])["environment"]["base_url"] == upstream


def test_auth_csrf_origin_pair_and_logout(tmp_path):
    app = create_app(tmp_path / "auth", vault=MemoryVault())
    with TestClient(app, base_url="http://127.0.0.1:18765") as c:
        assert c.get("/api/environments").status_code == 401
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/health", headers={"Host": "evil.example"}).status_code == 403
        code = app.state.auth.issue_code()
        assert (
            c.post(
                "/api/auth/pair",
                json={"code": code},
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert (
            c.post(
                "/api/auth/pair",
                json={"code": code},
                headers={"Origin": "http://127.0.0.1:18765"},
            ).status_code
            == 200
        )
        saved = c.cookies.get("api_post_session")
        assert c.get("/api/environments").status_code == 200
        assert (
            c.post(
                "/api/environments",
                json={"name": "x", "base_url": "http://127.0.0.1:8080"},
                headers={"Origin": "http://127.0.0.1:18765"},
            ).status_code
            == 403
        )
        csrf = c.get("/api/auth/session").json()["csrf"]
        assert (
            c.post(
                "/api/auth/logout",
                headers={"Origin": "http://127.0.0.1:18765", "X-CSRF-Token": csrf},
            ).status_code
            == 200
        )
        assert (
            c.get(
                "/api/environments", headers={"Cookie": "api_post_session=" + saved}
            ).status_code
            == 401
        )


def test_secrets_not_in_evidence(client, upstream):
    secret = "fixture-token-not-a-real-secret"
    meta = client.post(
        "/api/credentials", json={"name": "fixture", "value": secret}
    ).json()
    assert "value" not in meta
    env = environment(client, upstream, credential_ref=meta["id"])
    record = finish(
        client,
        submit(client, env, body='{"password":"fixture-password","message":"ok"}')[
            "id"
        ],
    )
    assert record["state"] == "completed", record
    combined = (
        "".join(
            p.read_text(encoding="utf-8", errors="ignore")
            for p in client.app.state.store.root.rglob("*.json")
        )
        + record["response_preview"]
    )
    assert secret not in combined and "fixture-password" not in combined
    assert record["request"]["headers"]["authorization"] == "[REDACTED]"
    assert "authorization" not in {
        k.lower() for k in record["request"]["configured_headers"]
    }
    assert "[REDACTED]" in combined
    query = finish(
        client, submit(client, env, path="/echo?token=fixture-query-secret")["id"]
    )
    assert "fixture-query-secret" not in json.dumps(query, ensure_ascii=False)


def test_duplicate_response_headers_are_preserved_and_cookies_masked(client):
    from http.server import BaseHTTPRequestHandler

    class HeaderHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Trace", "first")
            self.send_header("X-Trace", "second")
            self.send_header("Set-Cookie", "fixture=a; HttpOnly")
            self.send_header("Set-Cookie", "fixture2=b; HttpOnly")
            self.send_header("Content-Length", "11")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), HeaderHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = environment(client, "http://127.0.0.1:" + str(server.server_port))
        record = finish(client, submit(client, env)["id"])
        assert record["state"] == "completed", record
        headers = record["response_header_items"]
        assert [h["value"] for h in headers if h["name"] == "x-trace"] == [
            "first",
            "second",
        ]
        assert [h["value"] for h in headers if h["name"] == "set-cookie"] == [
            "[REDACTED]",
            "[REDACTED]",
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_recovery_preserves_record(tmp_path):
    root = tmp_path / "recover"
    store = Store(root)
    record, _ = store.create_run({"name": "recover"}, str(uuid4()), "digest")
    store.close()
    store = Store(root)
    assert store.run(record["id"])["state"] == "interrupted"
    assert store.list_runs()["total"] == 1
    store.close()


def test_remembered_browser_survives_restart_and_renews_legacy_cookie(tmp_path):
    vault = MemoryVault()
    root = tmp_path / "remembered"
    app = create_app(root, vault=vault)
    nonce = "legacy-browser"
    legacy = app.state.auth.sign_session(nonce, time.time() + 7 * 86400)
    with TestClient(app, base_url="http://127.0.0.1:18765") as client:
        client.cookies.set("api_post_session", legacy)
        response = client.get("/api/auth/session")
        assert response.status_code == 200
        assert "Max-Age=31536000" in response.headers["set-cookie"]
        remembered = response.cookies.get("api_post_session")
        expiry = int(remembered.split(":", 1)[0])
        assert expiry > time.time() + 360 * 86400
    next_app = create_app(root, vault=vault)
    with TestClient(next_app, base_url="http://127.0.0.1:18765") as client:
        client.cookies.set("api_post_session", remembered)
        assert client.get("/api/environments").status_code == 200
        csrf = client.get("/api/auth/session").json()["csrf"]
        assert (
            client.post(
                "/api/auth/logout",
                headers={"Origin": "http://127.0.0.1:18765", "X-CSRF-Token": csrf},
            ).status_code
            == 200
        )
        assert (
            client.get(
                "/api/environments",
                headers={"Cookie": "api_post_session=" + remembered},
            ).status_code
            == 401
        )


def test_short_secret_does_not_corrupt_metadata(client, upstream):
    env = environment(client, upstream)
    record = finish(client, submit(client, env, body='{"password":"1"}')["id"])
    assert record["environment_id"] == env["id"]
    assert record["request"]["environment_id"] == env["id"]
    assert record["state"] == "completed"


@pytest.mark.parametrize(
    "address,scope,port",
    [
        ("169.254.169.254", "internal", 80),
        ("::ffff:169.254.169.254", "internal", 80),
        ("127.0.0.1", "public", 443),
        ("10.0.0.1", "public", 443),
        ("0.0.0.0", "local", 8080),
        ("127.0.0.1", "local", 18765),
    ],
)
def test_forbidden_ip(address, scope, port):
    with pytest.raises(PolicyError):
        check_ip(address, scope, port, 18765)


def test_loopbacks_allowed():
    check_ip("127.0.0.1", "local", 8080, 18765)
    check_ip("::1", "local", 8080, 18765)


@pytest.mark.parametrize(
    "address", ["168.63.129.16", "100.100.100.200", "fd00:ec2::254"]
)
def test_metadata_special_addresses(address):
    with pytest.raises(PolicyError):
        check_ip(address, "internal", 80, 18765)


def test_dns_binding_validates_every_address(monkeypatch):
    async def scenario():
        backend = PinnedBackend(
            {"base_url": "https://example.com", "network_scope": "public"}, 18765
        )
        called = []
        loop = asyncio.get_running_loop()

        async def resolve(*args, **kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
            ]

        async def connect(*args, **kwargs):
            called.append(args)

        monkeypatch.setattr(loop, "getaddrinfo", resolve)
        monkeypatch.setattr(backend.backend, "connect_tcp", connect)
        with pytest.raises(PolicyError):
            await backend.connect_tcp("example.com", 443, timeout=1)
        assert not called

        async def good(*args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

        monkeypatch.setattr(loop, "getaddrinfo", good)
        await backend.connect_tcp("example.com", 443, timeout=1)
        assert called[0][0] == "93.184.216.34"

    asyncio.run(scenario())
