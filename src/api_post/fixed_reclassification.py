"""Apply a reviewed metadata-only placement plan, without changing request targets."""

from uuid import uuid5, NAMESPACE_URL
from .environment_groups import EnvironmentGroups
from .models import EnvironmentInput
from .network import validate_environment
from .storage import atomic, now

SUPPLIERS = "catalog/server/suppliers"


def apply_plan(store, vault, port, plan, backup):
    with store.lock:
        manager = EnvironmentGroups(store, vault, port)
        groups = manager.list()
        envs = store.envs()
        by_id = {e["id"]: e for e in envs}
        revisions = {e["id"]: e["revision"] for e in envs}
        if revisions != plan["revisions"]:
            raise ValueError("环境已变化，请重新核对归类清单")
        fixed = {
            m["environment"]["id"]
            for g in groups
            if g["kind"] == "fixed"
            for m in g["members"]
        }
        targets = {g["key"]: g for g in groups if g["kind"] != "fixed"}
        supplier_id = str(
            uuid5(NAMESPACE_URL, "api-post-environment-group:" + SUPPLIERS)
        )
        targets.setdefault(
            SUPPLIERS,
            {
                "id": supplier_id,
                "key": SUPPLIERS,
                "name": "供应商域名",
                "kind": "server",
                "region": "custom",
                "stage": "prod",
                "revision": 0,
                "variables": {},
                "secret_variables": [],
                "members": [],
                "conflicts": {},
            },
        )
        buckets = {}
        seen = set()
        for placement in plan["placements"]:
            id, key, variable = (
                placement["id"],
                placement["target"],
                placement["variable"],
            )
            if id not in fixed or id in seen or key not in targets:
                raise ValueError("归类来源、目标或重复连接不合法")
            seen.add(id)
            target = targets[key]
            if target["conflicts"]:
                raise ValueError("目标环境有未解决的变量冲突")
            if key not in buckets:
                buckets[key] = {
                    "group": target,
                    "variables": dict(target["variables"]),
                    "members": [
                        (m["environment"], m["variable"], True)
                        for m in target["members"]
                    ],
                }
            bucket = buckets[key]
            if variable in bucket["variables"]:
                raise ValueError("目标环境已有同名变量，请使用明确的平台或服务后缀")
            env = by_id[id]
            bucket["variables"][variable] = env["base_url"]
            bucket["members"].append(
                (env, variable, placement.get("connectable", True))
            )
        changes = []
        for bucket in buckets.values():
            target, variables = bucket["group"], bucket["variables"]
            EnvironmentInput.bounded_variables(variables)
            for env, variable, connectable in bucket["members"]:
                value = {
                    **env,
                    "name": (target["name"] + " / " + variable)[:80],
                    "variables": variables,
                    "secret_variables": target["secret_variables"],
                    "archived": not connectable,
                    "region": target["region"],
                    "stage": target["stage"],
                    "grouping": {
                        "key": target["key"],
                        "label": target["name"],
                        "variable": variable,
                        "kind": target["kind"],
                        "region": target["region"],
                        "stage": target["stage"],
                    },
                }
                # Credentials, protocol, hostname, port, connection IDs and import keys stay intact.
                if value["base_url"] != env["base_url"] or value.get(
                    "credential_ref"
                ) != env.get("credential_ref"):
                    raise ValueError("归类不能改变地址或凭证")
                EnvironmentInput.model_validate(
                    {k: value[k] for k in EnvironmentInput.model_fields}
                )
                validate_environment(value, port)
                changes.append(value)
        atomic(
            backup,
            {"created_at": now(), "plan": plan, "environments": envs, "groups": groups},
        )
        supplier = targets[SUPPLIERS]
        meta = {k: supplier[k] for k in ["id", "key", "name", "region", "stage"]}
        store.save_environment_group(changes, meta, revisions, supplier["revision"])
        return {
            "moved": len(seen),
            "updated_connections": len(changes),
            "before_active_connections": len(envs),
            "after_active_connections": len(store.envs()),
            "groups": [
                {
                    "name": g["name"],
                    "connections": len(g["members"]),
                    "variables": len(g["variables"]),
                }
                for g in manager.list()
            ],
        }
