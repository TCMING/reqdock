from test_workbench import client as client, upstream as upstream, environment
from api_post.fixed_reclassification import apply_plan, SUPPLIERS
from api_post.environment_groups import EnvironmentGroups, identity
from api_post.models import EnvironmentGroupUpdate


def test_reclassify_preserves_ids_addresses_and_import_keys(client, upstream, tmp_path):
    store = client.app.state.store
    vault = client.app.state.engine.vault
    original = environment(
        client, upstream, name="本地环境 / internal", variables={"internal": upstream}
    )
    store.save_env(
        {
            **original,
            "source": {"key": "fixture/server/0/internal", "group_label": "本地环境"},
        },
        original["id"],
        original["revision"],
    )
    fixed = environment(
        client, "https://example.com", name="fixed vendor", network_scope="public"
    )
    store.save_env(
        {**fixed, "source": {"key": "fixture/fixed/https://example.com"}},
        fixed["id"],
        fixed["revision"],
    )
    manager = EnvironmentGroups(store, vault, 18765)
    envs = store.envs()
    before = {e["id"]: e for e in envs}
    plan = {
        "revisions": {e["id"]: e["revision"] for e in envs},
        "placements": [{"id": fixed["id"], "target": SUPPLIERS, "variable": "平台名"}],
    }
    summary = apply_plan(store, vault, 18765, plan, tmp_path / "backup.json")
    assert summary["moved"] == 1
    saved = store.env(fixed["id"])
    assert saved["base_url"] == before[fixed["id"]]["base_url"]
    assert saved["source"]["key"] == before[fixed["id"]]["source"]["key"]
    assert identity(saved)[2:4] == ("平台名", "供应商域名")
    supplier = next(g for g in manager.list() if g["name"] == "供应商域名")
    data = {
        k: supplier[k]
        for k in [
            "name",
            "region",
            "stage",
            "variables",
            "secret_variables",
            "revision",
            "revisions",
        ]
    }
    data["connections"] = {
        "平台名": {
            k: saved[k]
            for k in [
                "network_scope",
                "credential_ref",
                "read_timeout",
                "total_timeout",
            ]
        }
    }
    result = manager.update(supplier["id"], EnvironmentGroupUpdate.model_validate(data))
    assert (
        result["members"][0]["environment"]["source"]["key"]
        == before[fixed["id"]]["source"]["key"]
    )


def test_disabled_template_remains_variable_but_not_connection(
    client, upstream, tmp_path
):
    store = client.app.state.store
    vault = client.app.state.engine.vault
    records = []
    for index in range(2):
        env = environment(client, upstream, name="fixed " + str(index))
        records.append(
            store.save_env(
                {**env, "source": {"key": "fixture/fixed/" + str(index)}},
                env["id"],
                env["revision"],
            )
        )
    plan = {
        "revisions": {e["id"]: e["revision"] for e in store.envs()},
        "placements": [
            {
                "id": e["id"],
                "target": SUPPLIERS,
                "variable": "平台" + str(i),
                "connectable": i == 0,
            }
            for i, e in enumerate(records)
        ],
    }
    apply_plan(store, vault, 18765, plan, tmp_path / "backup.json")
    group = EnvironmentGroups(store, vault, 18765).list()[0]
    assert len(group["variables"]) == 2 and len(group["members"]) == 1
    assert store.env(records[1]["id"])["archived"]
    assert len(store.envs(include_archived=True)) == 2
