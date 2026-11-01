import json
import pytest
from test_workbench import (
    client as client,
    upstream as upstream,
    environment,
    submit,
    finish,
)


def grouped(client, upstream):
    store = client.app.state.store
    for variable in ["a", "b"]:
        env = environment(
            client,
            upstream,
            name="fixture / " + variable,
            variables={"a": upstream, "b": upstream, "label": "old"},
        )
        store.save_env(
            {
                **env,
                "source": {
                    "key": "fixture/server/0/" + variable,
                    "group_label": "fixture",
                    "digest": "fixture",
                    "import_revision": env["revision"] + 1,
                },
            },
            env["id"],
            env["revision"],
        )
    return client.get("/api/environment-groups").json()[0]


def payload(group):
    return {
        k: group[k]
        for k in [
            "name",
            "region",
            "stage",
            "variables",
            "secret_variables",
            "revision",
            "revisions",
        ]
    } | {
        "connections": {
            m["variable"]: {
                k: m["environment"][k]
                for k in [
                    "network_scope",
                    "credential_ref",
                    "read_timeout",
                    "total_timeout",
                ]
            }
            for m in group["members"]
        }
    }


def update(client, group, data):
    return client.put("/api/environment-groups/" + group["id"], json=data)


def test_group_edit_updates_all_connections_and_preserves_ids(client, upstream):
    group = grouped(client, upstream)
    assert len(group["members"]) == 2 and len(group["variables"]) == 3
    data = payload(group)
    data["variables"] = {
        **data["variables"],
        "label": "new",
        "a": upstream + "/v2",
        "private": "fixture-group-private",
    }
    data["secret_variables"] = ["private"]
    result = update(client, group, data)
    assert result.status_code == 200, result.text
    saved = result.json()
    assert saved["revision"] == 1
    assert {m["environment"]["id"] for m in saved["members"]} == set(group["revisions"])
    for member in saved["members"]:
        env = member["environment"]
        assert env["variables"] == saved["variables"]
        assert env["base_url"] == saved["variables"][member["variable"]]
        assert env["revision"] == group["revisions"][env["id"]] + 1
    run = finish(
        client,
        submit(
            client, saved["members"][0]["environment"], body='{"label":"{{label}}"}'
        )["id"],
    )
    assert json.loads(run["response_preview"])["body"]["label"] == "new"
    assert "fixture-group-private" not in "".join(
        p.read_text(encoding="utf-8")
        for p in client.app.state.store.root.rglob("*.json")
    )
    assert update(client, group, data).status_code == 400


def test_legacy_single_connection_base_url_variable_matches_group_view(
    client, upstream
):
    env = environment(client, upstream)
    group = client.get("/api/environment-groups").json()[0]
    assert group["variables"]["base_url"] == upstream
    run = finish(client, submit(client, env, body='{"base":"{{base_url}}"}')["id"])
    assert run["state"] == "completed"
    assert json.loads(run["response_preview"])["body"]["base"] == upstream


def test_add_remove_service_and_preserve_old_history(client, upstream):
    group = grouped(client, upstream)
    data = payload(group)
    data["variables"]["c"] = upstream + "/third"
    data["connections"]["c"] = dict(data["connections"]["a"])
    response = update(client, group, data)
    assert response.status_code == 200, response.text
    group = response.json()
    c = next(m["environment"] for m in group["members"] if m["variable"] == "c")
    run = finish(client, submit(client, c)["id"])
    data = payload(group)
    data["variables"].pop("c")
    data["connections"].pop("c")
    assert update(client, group, data).status_code == 200
    assert c["id"] not in {e["id"] for e in client.get("/api/environments").json()}
    assert client.app.state.store.env(c["id"])["archived"]
    response = client.post(
        "/api/runs",
        json={
            "environment_id": c["id"],
            "submission_key": "00000000-0000-4000-8000-000000000001",
            "body": "{}",
        },
    )
    assert response.status_code == 400
    assert client.get("/api/runs/" + run["id"]).json()["state"] == "completed"
    remaining = client.get("/api/environment-groups").json()[0]["members"][0][
        "environment"
    ]
    invalid = client.post(
        "/api/runs",
        json={
            "environment_id": remaining["id"],
            "submission_key": "00000000-0000-4000-8000-000000000002",
            "path": "{{c}}/echo",
            "body": "{}",
        },
    )
    assert invalid.status_code == 400


def test_conflicts_need_explicit_resolution_and_invalid_batch_does_not_write(
    client, upstream
):
    group = grouped(client, upstream)
    env = group["members"][0]["environment"]
    client.app.state.store.save_env(
        {**env, "variables": {**env["variables"], "label": "different"}},
        env["id"],
        env["revision"],
    )
    group = client.get("/api/environment-groups").json()[0]
    assert "label" in group["conflicts"]
    data = payload(group)
    data["variables"]["label"] = "resolved"
    assert update(client, group, data).status_code == 400
    data["resolved_conflicts"] = ["label"]
    data["variables"]["a"] = "http://169.254.169.254/"
    assert update(client, group, data).status_code == 400
    assert (
        client.get("/api/environment-groups").json()[0]["revisions"]
        == group["revisions"]
    )
    data["variables"]["a"] = upstream
    response = update(client, group, data)
    assert response.status_code == 200, response.text
    assert not response.json()["conflicts"]


def test_committed_environment_batch_recovers_after_interrupted_current_write(
    client, upstream, monkeypatch
):
    from api_post import storage
    from api_post.environment_groups import EnvironmentGroups
    from api_post.models import EnvironmentGroupUpdate

    group = grouped(client, upstream)
    data = payload(group)
    data["variables"]["label"] = "recovered"
    original = storage.atomic
    failed = False

    def interrupted(path, value):
        nonlocal failed
        if not failed and path.name == "current.json" and "environments" in path.parts:
            failed = True
            raise OSError("fixture disk interruption")
        return original(path, value)

    store = client.app.state.store
    manager = EnvironmentGroups(store, client.app.state.engine.vault, 18765)
    monkeypatch.setattr(storage, "atomic", interrupted)
    with pytest.raises(OSError):
        manager.update(group["id"], EnvironmentGroupUpdate.model_validate(data))
    monkeypatch.setattr(storage, "atomic", original)
    # The next read completes the already committed transaction before exposing any member.
    group = manager.get(group["id"])
    assert group["revision"] == 1
    assert all(
        m["environment"]["variables"]["label"] == "recovered" for m in group["members"]
    )


def test_group_archive_preserves_files_and_requests_and_checks_revision(
    client, upstream
):
    group = grouped(client, upstream)
    env = group["members"][0]["environment"]
    run = finish(client, submit(client, env)["id"])
    path = "/api/environment-groups/" + group["id"] + "/archive"
    assert (
        client.post(
            path, json={"revision": 99, "revisions": group["revisions"]}
        ).status_code
        == 400
    )
    response = client.post(
        path, json={"revision": group["revision"], "revisions": group["revisions"]}
    )
    assert response.status_code == 200, response.text
    assert client.get("/api/environment-groups").json() == []
    assert client.get("/api/environments").json() == []
    assert client.app.state.store.env(env["id"])["archived"]
    assert client.get("/api/runs/" + run["id"]).json()["state"] == "completed"
