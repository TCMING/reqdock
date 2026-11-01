"""Application modules and model folders over existing multi-tag case membership."""

import json

MEMBERSHIP = "COALESCE(json_extract(summary,'$.groups'),json_array(json_extract(summary,'$.group')))"


def normalized(path):
    return "/".join(part.strip() for part in str(path).split("/") if part.strip())


class CaseModules:
    def __init__(self, db):
        roots = [
            "LLM调度器",
            "报警系统",
            "应用中心",
            "闲时调度器",
            "第三方服务（内部）",
            "第三方服务",
            "网关-ARK",
            "监控数据统计",
            "WALLE-同步调度器",
            "Doraemon-异步调度器",
            "ibrain接口测试",
            "海外-ai-Gateway",
        ]
        self.definitions = [
            {
                "id": "gpt",
                "name": "GPT模块",
                "prefixes": [
                    "Ibrain-GPT-Backend模块",
                    "ibrain接口测试/GPT工程",
                    "GPT工程",
                    "GPT模块",
                    "ibrain-gpt-backend",
                ],
            },
            {
                "id": "base",
                "name": "base",
                "prefixes": ["ibrain接口测试/base", "base", "ibrain-base"],
            },
            {
                "id": "prompt",
                "name": "Prompt工程",
                "prefixes": [
                    "ibrain接口测试/Prompt工程",
                    "Prompt工程",
                    "ibrain-prompt",
                ],
            },
        ]
        self.definitions += [
            {"id": "root:" + name, "name": name, "prefixes": [name]} for name in roots
        ]
        self.memberships = list(
            db.execute(
                "SELECT cases.id,membership.value FROM cases,json_each("
                + MEMBERSHIP
                + ") AS membership"
            )
        )
        self.groups = sorted({g for _, g in self.memberships if isinstance(g, str)})
        for path in self.groups:
            root = normalized(path).split("/")[0] or "未分组"
            if not self.owner(root):
                self.definitions.append(
                    {"id": "root:" + root, "name": root, "prefixes": [root]}
                )
        ids = {d["id"]: set() for d in self.definitions}
        for id, path in self.memberships:
            definition = self.owner(path)
            if definition:
                ids[definition["id"]].add(id)
        self.modules = [
            {"id": d["id"], "name": d["name"], "count": len(ids[d["id"]])}
            for d in self.definitions
        ]

    def owner(self, path):
        path = normalized(path) or "未分组"
        matches = [
            (len(prefix), d)
            for d in self.definitions
            for prefix in d["prefixes"]
            if path == prefix or path.startswith(prefix + "/")
        ]
        return max(matches, key=lambda x: x[0])[1] if matches else None

    def relative(self, path, definition):
        path = normalized(path) or "未分组"
        matches = [
            p for p in definition["prefixes"] if path == p or path.startswith(p + "/")
        ]
        if not matches:
            return None
        prefix = max(matches, key=len)
        return path[len(prefix) :].lstrip("/")

    def select(self, module, group):
        definition = None
        if module:
            matches = [
                d for d in self.definitions if d["id"] == module or d["name"] == module
            ]
            if len(matches) != 1:
                raise ValueError("模块不存在或名称不唯一，请刷新后选择")
            definition = matches[0]
        owned = [
            g
            for g in self.groups
            if not definition or self.owner(g)["id"] == definition["id"]
        ]
        labels = {}
        options = []
        if definition:
            relatives = set()
            for g in owned:
                parts = self.relative(g, definition).split("/")
                for i in range(1, len(parts) + 1):
                    relatives.add("/".join(parts[:i]))
            for rel in sorted(relatives):
                value = definition["prefixes"][0] + ("/" + rel if rel else "")
                options.append(value)
                labels[value] = rel or "未分组"
        else:
            options = self.groups
            labels = {g: g for g in options}
        allowed = owned
        if group:
            if definition:
                wanted = self.relative(group, definition)
                if wanted is None:
                    wanted = "" if group == "未分组" else normalized(group)
                allowed = [
                    g
                    for g in owned
                    if wanted is not None
                    and (
                        self.relative(g, definition) == wanted
                        or bool(wanted)
                        and self.relative(g, definition).startswith(wanted + "/")
                    )
                ]
            else:
                wanted = normalized(group)
                allowed = [
                    g
                    for g in owned
                    if normalized(g) == wanted or normalized(g).startswith(wanted + "/")
                ]
        return {
            "groups": options,
            "group_labels": labels,
            "modules": self.modules,
        }, json.dumps(allowed, ensure_ascii=False)

    def decorate(self, item):
        memberships = {}
        for path in item.get("groups", [item.get("group", "未分组")]):
            module = self.owner(path)
            if not module:
                continue
            entry = memberships.setdefault(
                module["id"], {"id": module["id"], "name": module["name"], "groups": []}
            )
            relative = self.relative(path, module) or "未分组"
            if relative not in entry["groups"]:
                entry["groups"].append(relative)
        return {**item, "modules": list(memberships.values())}
