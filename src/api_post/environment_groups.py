"""Grouped editing of shared variables with recoverable multi-connection commits."""

import json
import re
from uuid import uuid4, uuid5, NAMESPACE_URL
from .models import EnvironmentInput
from .network import validate_environment
from .openapi_import import Importer
from .security import sensitive


def protect_variables(store, vault, port, variables, secrets):
    if any(key not in variables for key in secrets):
        raise ValueError("保密变量必须存在于变量列表中")
    protector = Importer(store, vault, port)
    protector.discover(variables)
    for key, item in variables.items():
        if sensitive(key) or key in secrets:
            if "{{" in item and not re.fullmatch(r"\{\{secret:[0-9a-f-]{36}\}\}", item):
                raise ValueError("保密变量请填写实际值或保留已有秘密引用")
            protector.secret(item)
    return {
        key: protector.secret(item)
        if sensitive(key) or key in secrets
        else protector.private_template(protector.url(item))
        if item.startswith(("http://", "https://"))
        else protector.private_template(item)
        for key, item in variables.items()
    }


def identity(env):
    assigned = env.get("grouping")
    if assigned:
        key = assigned["key"]
        return (
            str(uuid5(NAMESPACE_URL, "api-post-environment-group:" + key)),
            key,
            assigned["variable"],
            assigned["label"],
            assigned.get("kind", "server"),
        )
    source = env.get("source", {}).get("key", "")
    name = re.sub(r"^AI服务平台\s*/\s*", "", env["name"])
    match = re.fullmatch(r"(.*\/server\/[^/]+)\/([^/]+)", source)
    if match:
        key, variable = match.groups()
        suffix = " / " + variable
        label = env.get("source", {}).get("group_label") or (
            name[: -len(suffix)] if name.endswith(suffix) else name
        )
        kind = "local" if key.startswith("local/") else "server"
    elif "/fixed/" in source:
        key, variable, label, kind = (
            source.split("/fixed/")[0] + "/fixed",
            env["id"],
            "固定地址",
            "fixed",
        )
    else:
        key, variable, label, kind = (
            "local/" + env["id"] + "/server/0",
            "base_url",
            name,
            "local",
        )
    return (
        str(uuid5(NAMESPACE_URL, "api-post-environment-group:" + key)),
        key,
        variable,
        label,
        kind,
    )


class EnvironmentGroups:
    def __init__(self, store, vault, port):
        self.store, self.vault, self.port = store, vault, port

    def list(self):
        with self.store.lock:
            groups = {}
            for env in self.store.envs():
                id, key, variable, label, kind = identity(env)
                groups.setdefault(
                    id,
                    {"id": id, "key": key, "name": label, "kind": kind, "members": []},
                )["members"].append({"variable": variable, "environment": env})
            result = []
            for group in groups.values():
                path = (
                    self.store.path("environment_groups", group["id"]) / "current.json"
                )
                meta = (
                    json.loads(path.read_text(encoding="utf-8"))
                    if path.exists()
                    else {}
                )
                first = group["members"][0]["environment"]
                group.update(
                    revision=meta.get("revision", 0),
                    name=meta.get("name", group["name"]),
                    region=meta.get(
                        "region",
                        first.get("grouping", {}).get("region", first["region"]),
                    ),
                    stage=meta.get(
                        "stage", first.get("grouping", {}).get("stage", first["stage"])
                    ),
                )
                values, secret_names = {}, set()
                for member in group["members"]:
                    env = member["environment"]
                    secret_names.update(env.get("secret_variables", []))
                    for key, value in env.get("variables", {}).items():
                        values.setdefault(key, set()).add(value)
                if group["kind"] != "fixed":
                    # The actual service connection is authoritative for its own address.
                    for member in group["members"]:
                        values[member["variable"]] = {member["environment"]["base_url"]}
                group["conflicts"] = {
                    k: sorted(v) for k, v in values.items() if len(v) > 1
                }
                group["variables"] = {
                    k: next(iter(v)) if len(v) == 1 else "" for k, v in values.items()
                }
                group["secret_variables"] = sorted(secret_names)
                group["revisions"] = {
                    m["environment"]["id"]: m["environment"]["revision"]
                    for m in group["members"]
                }
                result.append(group)
            return result

    def get(self, id):
        group = next((g for g in self.list() if g["id"] == str(id)), None)
        if not group:
            raise KeyError("环境组不存在")
        return group

    def archive(self, id, model):
        data = model.model_dump(mode="json")
        with self.store.lock:
            group = self.get(id)
            if (
                data["revision"] != group["revision"]
                or data["revisions"] != group["revisions"]
            ):
                raise ValueError("环境组已变化，请刷新后操作")
            changes = [{**m["environment"], "archived": True} for m in group["members"]]
            meta = {k: group[k] for k in ["id", "key", "name", "region", "stage"]}
            meta["archived"] = True
            self.store.save_environment_group(
                changes, meta, group["revisions"], group["revision"]
            )
            return {"id": str(id), "archived": True, "connections": len(changes)}

    def update(self, id, model):
        data = model.model_dump(mode="json")
        with self.store.lock:
            group = self.get(id)
            if group["kind"] == "fixed":
                raise ValueError("固定地址连接请逐项编辑连接设置")
            if (
                data["revisions"] != group["revisions"]
                or data["revision"] != group["revision"]
            ):
                raise ValueError("环境组已被修改，请刷新后再保存")
            if set(group["conflicts"]) - set(data["resolved_conflicts"]):
                raise ValueError("请先明确选择存在冲突的变量值")
            if not data["name"].strip():
                raise ValueError("环境名称不能为空")
            if any(key not in data["variables"] for key in data["connections"]):
                raise ValueError("服务地址必须对应一个环境变量")
            credentials = {c["id"] for c in self.store.credential_meta()}
            for key, settings in data["connections"].items():
                if (
                    sensitive(key)
                    or key in data["secret_variables"]
                    or "{{" in data["variables"][key]
                ):
                    raise ValueError("服务地址不能是保密值或引用")
                validate_environment(
                    {"base_url": data["variables"][key], **settings}, self.port
                )
                if (
                    settings["credential_ref"]
                    and settings["credential_ref"] not in credentials
                ):
                    raise ValueError("服务连接的凭证不存在")
            variables = protect_variables(
                self.store,
                self.vault,
                self.port,
                data["variables"],
                data["secret_variables"],
            )
            old = {m["variable"]: m["environment"] for m in group["members"]}
            changes = []
            for key, settings in data["connections"].items():
                env = old.get(key, {"id": str(uuid4())})
                value = {
                    **env,
                    **settings,
                    "name": (data["name"] + " / " + key)[:80],
                    "base_url": variables[key],
                    "variables": variables,
                    "secret_variables": data["secret_variables"],
                    "region": data["region"],
                    "stage": data["stage"],
                    "archived": False,
                    "source": {
                        **env.get("source", {}),
                        "key": env["source"]["key"]
                        if env.get("grouping")
                        else group["key"] + "/" + key,
                        "group_label": data["name"],
                    },
                }
                if env.get("grouping"):
                    value["grouping"] = {
                        "key": group["key"],
                        "label": data["name"],
                        "variable": key,
                        "kind": group["kind"],
                        "region": data["region"],
                        "stage": data["stage"],
                    }
                # All fields are checked before committing any member.
                try:
                    EnvironmentInput.model_validate(
                        {k: value[k] for k in EnvironmentInput.model_fields}
                    )
                    validate_environment(value, self.port)
                except ValueError:
                    raise ValueError(
                        "服务连接配置不合法，请检查地址长度、网络范围及变量大小"
                    ) from None
                changes.append(value)
            for key, env in old.items():
                if key not in data["connections"]:
                    changes.append({**env, "archived": True})
            meta = {
                "id": group["id"],
                "key": group["key"],
                "name": data["name"].strip(),
                "region": data["region"],
                "stage": data["stage"],
            }
            self.store.save_environment_group(
                changes, meta, data["revisions"], data["revision"]
            )
            return self.get(id)
