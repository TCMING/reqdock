import json
from test_workbench import client as client, upstream as upstream
from uuid import uuid4
from api_post.private_data import transform
from api_post.storage import atomic
from test_workbench import finish


def document(url):
    return {
        "openapi": "3.0.3",
        "info": {"title": "fixture-project", "version": "1"},
        "servers": [
            {
                "description": "本地环境",
                "url": "",
                "variables": {"ibrain-common": {"default": url}},
            }
        ],
        "paths": {
            "{{ibrain-common}}/echo": {
                "post": {
                    "x-target-id": "echo",
                    "summary": "导入 JSON",
                    "tags": ["基础请求"],
                    "parameters": [
                        {
                            "in": "header",
                            "name": "X-Api-Key",
                            "example": "fixture-header-secret",
                        }
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "example": '{"prompt_token":"fixture-body-secret","message":"hello"}'
                            }
                        }
                    },
                }
            },
            "{{ibrain-common}}/form": {
                "post": {
                    "x-target-id": "form",
                    "summary": "导入表单",
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "properties": {
                                        "prompt_token": {
                                            "example": "fixture-form-secret"
                                        }
                                    }
                                }
                            }
                        }
                    },
                }
            },
            "{{ibrain-common}}/lookup/{id}": {
                "get": {
                    "x-target-id": "lookup",
                    "summary": "路径参数",
                    "parameters": [{"in": "path", "name": "id", "example": ""}],
                }
            },
        },
    }


def test_multiple_groups_and_legacy_repair_preserve_local_edits(client, upstream):
    doc = document(upstream)
    tags = ["图片/banana", "视频/万相", "literal%_group"]
    doc["paths"]["{{ibrain-common}}/echo"]["post"]["tags"] = tags

    def run_import():
        r = client.post(
            "/api/import/openapi", json={"document": doc, "source_id": "groups-fixture"}
        )
        assert r.status_code == 200, r.text
        return r.json()

    run_import()
    rows = client.get("/api/cases", params={"group": tags[1]}).json()
    assert rows["total"] == 1
    case = client.get("/api/cases/" + rows["items"][0]["id"]).json()
    assert case["group"] == tags[0] and case["groups"] == tags
    assert client.get("/api/cases", params={"group": tags[2]}).json()["total"] == 1
    assert " / ".join(tags) not in rows["groups"]
    request = {
        **case["request"],
        "name": "local edited",
        "body": '{"message":"keep me"}',
    }
    edited = client.put(
        "/api/cases/" + case["id"],
        json={"revision": case["revision"], "request": request},
    ).json()
    # Reproduce the previous importer format while retaining an independently edited request.
    edited.pop("groups")
    edited["group"] = " / ".join(tags)
    store = client.app.state.store
    atomic(store.path("cases", case["id"]) / "current.json", edited)
    store.index_case(edited)
    result = run_import()
    assert result["cases"]["regrouped"] == 1
    repaired = client.get("/api/cases/" + case["id"]).json()
    assert repaired["groups"] == tags and repaired["group"] == tags[0]
    assert repaired["request"] == edited["request"]
    assert repaired["source"]["import_revision"] == edited["source"]["import_revision"]
    assert repaired["revision"] == edited["revision"] + 1
    assert run_import()["cases"]["unchanged"] == 3


def test_import_preserves_secrets_and_is_idempotent(client, upstream):
    doc = document(upstream)
    first = client.post(
        "/api/import/openapi", json={"document": doc, "source_id": "fixture"}
    )
    assert first.status_code == 200, first.text
    report = first.json()
    assert report["operations"] == 3 and report["cases"]["created"] == 3
    assert report["secret_count"] == 3
    assert client.get("/api/runs").json()["total"] == 0
    rows = client.get("/api/cases?search=导入").json()
    assert rows["total"] == 2
    case = next(c for c in rows["items"] if c["name"] == "导入 JSON")
    case = client.get("/api/cases/" + case["id"]).json()
    assert "{{secret:" in case["request"]["body"]
    env = client.get("/api/environments").json()[0]
    request = {
        **case["request"],
        "environment_id": env["id"],
        "submission_key": str(uuid4()),
        "case_id": case["id"],
    }
    result = client.post("/api/runs", json=request)
    assert result.status_code == 200, result.text
    record = finish(client, result.json()["id"])
    assert record["state"] == "completed", record
    raw = (
        "".join(
            p.read_text(encoding="utf-8")
            for p in client.app.state.store.root.rglob("*.json")
        )
        + record["response_preview"]
    )
    for secret in [
        "fixture-header-secret",
        "fixture-body-secret",
        "fixture-form-secret",
    ]:
        assert secret not in raw
    root = client.app.state.store.root
    encrypted = (root / "imports" / report["id"] / "source.dpapi").read_bytes()
    assert b"fixture-body-secret" not in encrypted
    assert json.loads(transform(encrypted, decrypt=True)) == doc
    second = client.post(
        "/api/import/openapi", json={"document": doc, "source_id": "fixture"}
    ).json()
    assert second["cases"] == {"unchanged": 3}
    assert client.get("/api/cases").json()["total"] == 3
    assert len(client.get("/api/environments").json()) == 1


def test_unimplemented_form_is_not_sent(client, upstream):
    client.post(
        "/api/import/openapi",
        json={"document": document(upstream), "source_id": "fixture"},
    )
    rows = client.get("/api/cases").json()["items"]
    case = next(c for c in rows if c["name"] == "导入表单")
    detail = client.get("/api/cases/" + case["id"]).json()
    env = client.get("/api/environments").json()[0]
    response = client.post(
        "/api/runs",
        json={
            **detail["request"],
            "case_id": case["id"],
            "environment_id": env["id"],
            "submission_key": str(uuid4()),
        },
    )
    assert response.status_code == 400
    assert client.get("/api/runs").json()["total"] == 0


def test_missing_path_and_variable_do_not_send(client, upstream):
    env = client.post(
        "/api/environments", json={"name": "local", "base_url": upstream}
    ).json()
    for path in ["/lookup/{id}", "/echo?foo={{missing}}"]:
        response = client.post(
            "/api/runs",
            json={
                "environment_id": env["id"],
                "submission_key": str(uuid4()),
                "path": path,
                "body": "{}",
            },
        )
        assert response.status_code == 400
    assert client.get("/api/runs").json()["total"] == 0


def test_cross_operation_secret_is_protected_before_writing(client, upstream):
    doc = document(upstream)
    doc["paths"]["{{ibrain-common}}/copy"] = {
        "post": {
            "x-target-id": "copy",
            "summary": "复用配置",
            "requestBody": {
                "content": {
                    "application/json": {
                        "example": '{"unusual_name":"fixture-body-secret","count":1}'
                    }
                }
            },
        }
    }
    result = client.post(
        "/api/import/openapi", json={"document": doc, "source_id": "fixture"}
    )
    assert result.status_code == 200, result.text
    copied = next(
        c for c in client.get("/api/cases").json()["items"] if c["name"] == "复用配置"
    )
    detail = client.get("/api/cases/" + copied["id"]).json()
    assert "fixture-body-secret" not in detail["request"]["body"]
    assert json.loads(detail["request"]["body"])["count"] == 1
    assert "{{secret:" in detail["request"]["body"]
