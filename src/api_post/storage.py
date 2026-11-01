import json
import os
import re
import sqlite3
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from .case_taxonomy import CaseTaxonomy

ID = re.compile(r"^[0-9a-f-]{36}$")


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._environment_transactions_pending = True
        self.db = sqlite3.connect(self.root / "index.sqlite3", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=3000")
        self.db.execute("PRAGMA cache_size=-2048")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, submission_key TEXT UNIQUE, digest TEXT, state TEXT, created TEXT, summary TEXT)"
        )
        self.db.commit()
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, source_key TEXT UNIQUE, summary TEXT)"
        )
        self.db.commit()
        self.recover()

    def path(self, section, id):
        if section not in {
            "environments",
            "runs",
            "credentials",
            "cases",
            "imports",
            "resources",
            "domains",
            "environment_groups",
        } or not ID.fullmatch(str(id)):
            raise ValueError("无效记录 ID")
        p = self.root / section / str(id)
        if not p.resolve().is_relative_to(self.root):
            raise ValueError("路径越界")
        return p

    def envs(self, include_archived=False):
        with self.lock:
            self.recover_environment_transactions()
            values = [
                json.loads(p.read_text(encoding="utf-8"))
                for p in (self.root / "environments").glob("*/current.json")
            ]
            return sorted(
                [e for e in values if include_archived or not e.get("archived", False)],
                key=lambda e: e["created_at"],
            )

    def env(self, id):
        with self.lock:
            self.recover_environment_transactions()
            path = self.path("environments", id) / "current.json"
            if not path.exists():
                raise KeyError("环境不存在")
            return json.loads(path.read_text(encoding="utf-8"))

    def recover_environment_transactions(self):
        with self.lock:
            if not self._environment_transactions_pending:
                return
            for plan in (self.root / "environment-transactions").glob("*.json"):
                transaction = json.loads(plan.read_text(encoding="utf-8"))
                if transaction.get("complete"):
                    continue
                for item in transaction["members"]:
                    if (
                        item["section"] not in {"environments", "environment_groups"}
                        or not isinstance(item["revision"], int)
                        or item["revision"] < 1
                    ):
                        raise ValueError("环境事务记录不合法")
                    folder = self.path(item["section"], item["id"])
                    value = json.loads(
                        (folder / "revisions" / f"{item['revision']}.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    atomic(folder / "current.json", value)
                transaction["complete"] = True
                atomic(plan, transaction)
            self._environment_transactions_pending = False

    def save_environment_group(self, changes, group, revisions, group_revision):
        with self.lock:
            self.recover_environment_transactions()
            for id, revision in revisions.items():
                if self.env(id)["revision"] != revision:
                    raise ValueError("环境组已被修改，请刷新后再保存")
            group_folder = self.path("environment_groups", group["id"])
            current = group_folder / "current.json"
            old_group = (
                json.loads(current.read_text(encoding="utf-8"))
                if current.exists()
                else None
            )
            if (old_group["revision"] if old_group else 0) != group_revision:
                raise ValueError("环境组已被修改，请刷新后再保存")
            estimate = sum(
                len(json.dumps(v, ensure_ascii=False).encode()) for v in changes
            )
            if shutil.disk_usage(self.root).free < 1024**3 + estimate * 3:
                raise ValueError("环境组保存所需磁盘空间不足")
            members = []
            for value in changes:
                id = value["id"]
                folder = self.path("environments", id)
                old = self.env(id) if (folder / "current.json").exists() else None
                value = {
                    **value,
                    "schema_version": 1,
                    "revision": old["revision"] + 1 if old else 1,
                    "created_at": old["created_at"] if old else now(),
                    "updated_at": now(),
                }
                atomic(folder / "revisions" / f"{value['revision']}.json", value)
                members.append(
                    {"section": "environments", "id": id, "revision": value["revision"]}
                )
            group = {
                **group,
                "schema_version": 1,
                "revision": group_revision + 1,
                "updated_at": now(),
            }
            atomic(group_folder / "revisions" / f"{group['revision']}.json", group)
            members.append(
                {
                    "section": "environment_groups",
                    "id": group["id"],
                    "revision": group["revision"],
                }
            )
            self._environment_transactions_pending = True
            atomic(
                self.root / "environment-transactions" / (str(uuid4()) + ".json"),
                {"members": members, "complete": False},
            )
            self.recover_environment_transactions()

    def save_env(self, data, id=None, revision=None):
        with self.lock:
            old = self.env(id) if id else None
            if old and revision != old["revision"]:
                raise ValueError("环境已被修改，请刷新后再保存")
            id = str(id or uuid4())
            r = (old["revision"] if old else 0) + 1
            value = {
                **data,
                "id": id,
                "revision": r,
                "created_at": old["created_at"] if old else now(),
                "updated_at": now(),
                "schema_version": 1,
            }
            if old and "source" in old and "source" not in value:
                value["source"] = old["source"]
            if old and "grouping" in old and "grouping" not in value:
                value["grouping"] = old["grouping"]
            folder = self.path("environments", id)
            atomic(folder / "revisions" / f"{r}.json", value)
            atomic(folder / "current.json", value)
            return value

    def credential_meta(self):
        with self.lock:
            return [
                json.loads(p.read_text(encoding="utf-8"))
                for p in (self.root / "credentials").glob("*/current.json")
            ]

    def save_credential_meta(self, data):
        atomic(self.path("credentials", data["id"]) / "current.json", data)

    def case(self, id):
        with self.lock:
            path = self.path("cases", id) / "current.json"
            if not path.exists():
                raise KeyError("用例不存在")
            return json.loads(path.read_text(encoding="utf-8"))

    def response_examples(self, case_id):
        with self.lock:
            self.case(case_id)
            path = self.path("cases", case_id) / "response-examples.json"
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    def save_response_example(self, case_id, run_id):
        with self.lock:
            case = self.case(case_id)
            run = self.run(run_id)
            if run.get("request", {}).get("case_id") != case_id:
                raise ValueError("此请求记录不属于当前用例，请从已保存的用例发送请求后再保存结果")
            if run["state"] in {"queued", "running"}:
                raise ValueError("请等待请求结束后再保存结果示例")
            folder = self.path("runs", run_id)
            if not run.get("status_code") and not any((folder / name).exists() for name in
                    ("response.body.txt", "stream.events.ndjson")):
                raise ValueError("此记录未收到响应，不能保存为结果示例")
            items = self.response_examples(case_id)
            for item in items:
                if item["run_id"] == run_id:
                    return item
            if len(items) >= 100:
                raise ValueError("每个用例最多保存100份结果示例")
            item = {"id": str(uuid4()), "run_id": run_id, "case_id": case_id,
                    "case_revision": case["revision"], "saved_at": now(),
                    "name": run["name"], "created_at": run["created_at"],
                    "environment_name": run["environment_name"],
                    "status_code": run.get("status_code"), "state": run["state"],
                    "is_sse": run.get("is_sse", False)}
            atomic(self.path("cases", case_id) / "response-examples.json", [item, *items])
            return item

    def response_example(self, case_id, example_id):
        with self.lock:
            for item in self.response_examples(case_id):
                if item["id"] == example_id:
                    run = self.run(item["run_id"])
                    if run.get("request", {}).get("case_id") != case_id:
                        raise ValueError("结果示例与用例关联不一致")
                    return item, run
            raise KeyError("结果示例不存在")

    def case_summary(self, value):
        return {
            k: v
            for k, v in value.items()
            if k not in {"request", "parameters", "description"}
        }

    def save_case(self, request, group, id=None, revision=None):
        with self.lock:
            old = self.case(id) if id else None
            if old and revision != old["revision"]:
                raise ValueError("用例已被修改，请重新载入后保存")
            id = str(id or uuid4())
            r = old["revision"] + 1 if old else 1
            value = {
                **(old or {}),
                "id": id,
                "revision": r,
                "schema_version": 1,
                "name": request["name"],
                "method": request["method"],
                "path_preview": request["path"],
                "request": request,
                "group": old["group"] if old else group,
                "resource_ids": list(
                    {x["resource_id"] for x in request.get("resource_bindings", [])}
                ),
                "created_at": old["created_at"] if old else now(),
                "updated_at": now(),
            }
            if not old:
                value.update(
                    source={"key": "local:" + id, "project": "本地", "digest": ""},
                    environment_ids=[],
                    warnings=[],
                    blocking=[],
                    server_variable=None,
                )
            folder = self.path("cases", id)
            atomic(folder / "revisions" / f"{r}.json", value)
            atomic(folder / "current.json", value)
            self.index_case(value)
            return value

    def index_case(self, value):
        with self.lock:
            self.db.execute(
                "INSERT INTO cases VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET summary=excluded.summary",
                (
                    value["id"],
                    value["source"]["key"],
                    json.dumps(self.case_summary(value), ensure_ascii=False),
                ),
            )
            self.db.commit()

    def save_imported_case(self, value):
        with self.lock:
            row = self.db.execute(
                "SELECT id FROM cases WHERE source_key=?", (value["source"]["key"],)
            ).fetchone()
            old = self.case(row[0]) if row else None
            if old and old["source"]["digest"] == value["source"]["digest"]:
                groups = value.get("groups")
                if (
                    groups
                    and "groups" not in old
                    and old["group"] == " / ".join(groups)
                ):
                    # Metadata-only repair: preserve local request edits and conflict tracking.
                    was_imported = old["revision"] == old["source"].get(
                        "import_revision", old["revision"]
                    )
                    old.update(
                        group=groups[0],
                        groups=groups,
                        revision=old["revision"] + 1,
                        updated_at=now(),
                    )
                    if was_imported:
                        old["source"]["import_revision"] = old["revision"]
                    folder = self.path("cases", old["id"])
                    atomic(folder / "revisions" / f"{old['revision']}.json", old)
                    atomic(folder / "current.json", old)
                    self.index_case(old)
                    return old, "regrouped"
                return old, "unchanged"
            if old and old["revision"] != old["source"].get(
                "import_revision", old["revision"]
            ):
                return old, "conflict"
            id = old["id"] if old else str(uuid4())
            revision = old["revision"] + 1 if old else 1
            value.update(
                id=id,
                revision=revision,
                schema_version=1,
                created_at=old["created_at"] if old else now(),
                updated_at=now(),
            )
            value["source"]["import_revision"] = revision
            folder = self.path("cases", id)
            atomic(folder / "revisions" / f"{revision}.json", value)
            atomic(folder / "current.json", value)
            self.index_case(value)
            return value, "updated" if old else "created"

    def case_taxonomy(self):
        path = self.root / "case-taxonomy.json"
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        return CaseTaxonomy(self.db, state)

    def taxonomy_view(self):
        with self.lock:
            return self.case_taxonomy().view()

    def taxonomy_update(self, command):
        with self.lock:
            catalog = self.case_taxonomy()
            catalog.mutate(command)
            atomic(self.root / "case-taxonomy.json", catalog.data)
            return catalog.view()

    def classify_models(self, revision):
        from .model_classification import reclassify
        with self.lock:
            catalog = self.case_taxonomy()
            if catalog.data["revision"] != revision:
                raise ValueError("分类已被修改，请刷新后重试")
            cases = [self.case(row[0]) for row in self.db.execute("SELECT id FROM cases").fetchall()]
            before = catalog.data
            # Keep a complete recoverable classification snapshot, without request bodies.
            import copy
            backup = copy.deepcopy(before)
            result = reclassify(catalog, cases)
            if result["changed"]:
                atomic(self.root / "case-classification-backups" / (uuid4().hex+".json"), backup)
                atomic(self.root / "case-taxonomy.json", catalog.data)
            return result

    def classify_case(self, id, revision, case_revision, memberships):
        with self.lock:
            if self.case(id)["revision"] != case_revision:
                raise ValueError("用例已被修改，请刷新后调整归属")
            catalog = self.case_taxonomy()
            catalog.assign(id, revision, memberships)
            atomic(self.root / "case-taxonomy.json", catalog.data)
            return catalog.decorate(self.case(id))

    def classified_case(self, id):
        with self.lock:
            return self.case_taxonomy().decorate(self.case(id))

    def list_cases(self, page=1, limit=50, search="", group="", module=""):
        with self.lock:
            catalog = self.case_taxonomy()
            navigation, allowed = catalog.select(module, group)
            where = []
            params = []
            if search:
                where.append(
                    "(json_extract(summary,'$.name') LIKE ? ESCAPE '\\' OR json_extract(summary,'$.path_preview') LIKE ? ESCAPE '\\')"
                )
                term = (
                    "%"
                    + search.replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                    + "%"
                )
                params.extend([term, term])
            if group or module:
                where.append("id IN (SELECT value FROM json_each(?))")
                params.append(allowed)
            clause = " WHERE " + " AND ".join(where) if where else ""
            total = self.db.execute(
                "SELECT COUNT(*) FROM cases" + clause, params
            ).fetchone()[0]
            rows = self.db.execute(
                "SELECT summary FROM cases"
                + clause
                + " ORDER BY json_extract(summary,'$.group'),json_extract(summary,'$.name') LIMIT ? OFFSET ?",
                [*params, limit, (page - 1) * limit],
            ).fetchall()
            return {
                "items": [catalog.decorate(json.loads(r[0])) for r in rows],
                "total": total,
                "page": page,
                "limit": limit,
                **navigation,
            }

    def create_run(self, snapshot, key, digest):
        with self.lock:
            row = self.db.execute(
                "SELECT id,digest FROM runs WHERE submission_key=?", (key,)
            ).fetchone()
            if row:
                if row[1] != digest:
                    raise ValueError("相同提交标识对应不同请求")
                return self.run(row[0]), False
            id = str(uuid4())
            folder = self.path("runs", id)
            value = {
                "schema_version": 1,
                "id": id,
                "submission_key": key,
                "digest": digest,
                "state": "queued",
                "created_at": now(),
                "validation_result": "unknown",
                **snapshot,
            }
            atomic(folder / "manifest.json", value)
            try:
                self.index(value)
            except Exception:
                value["state"] = "interrupted"
                value["error"] = "索引创建失败，未执行请求"
                atomic(folder / "manifest.json", value)
                raise
            return value, True

    def index(self, value):
        with self.lock:
            summary = {
                k: v
                for k, v in value.items()
                if k
                not in {
                    "request",
                    "environment",
                    "response_headers",
                    "response_header_items",
                }
            }
            summary["snapshot_bytes"] = (
                (self.path("runs", value["id"]) / "manifest.json").stat().st_size
            )
            self.db.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,summary=excluded.summary",
                (
                    value["id"],
                    value["submission_key"],
                    value["digest"],
                    value["state"],
                    value["created_at"],
                    json.dumps(summary, ensure_ascii=False),
                ),
            )
            self.db.commit()

    def run(self, id):
        with self.lock:
            p = self.path("runs", id) / "manifest.json"
            if not p.exists():
                raise KeyError("记录不存在")
            return json.loads(p.read_text(encoding="utf-8"))

    def update_run(self, value):
        with self.lock:
            atomic(self.path("runs", value["id"]) / "manifest.json", value)
            self.index(value)

    def list_runs(self, page=1, limit=50, environment_id=None):
        with self.lock:
            # SQLite JSON projection avoids loading response bodies or all history.
            where = (
                "WHERE json_extract(summary,'$.environment_id')=?"
                if environment_id
                else ""
            )
            params = [environment_id] if environment_id else []
            total = self.db.execute(
                "SELECT COUNT(*) FROM runs " + where, params
            ).fetchone()[0]
            rows = self.db.execute(
                "SELECT summary FROM runs "
                + where
                + " ORDER BY created DESC LIMIT ? OFFSET ?",
                [*params, limit, (page - 1) * limit],
            ).fetchall()
            return {
                "items": [json.loads(r[0]) for r in rows],
                "total": total,
                "page": page,
                "limit": limit,
            }

    def recover(self):
        with self.lock:
            # Reconcile only small manifests; never read response bodies or media.
            for p in (self.root / "runs").glob("*/manifest.json"):
                value = json.loads(p.read_text(encoding="utf-8"))
                if value["state"] in {"queued", "running"}:
                    value.update(
                        state="interrupted",
                        error="服务已重启，未自动重新发送",
                        ended_at=now(),
                    )
                    atomic(p, value)
                self.index(value)
            for p in (self.root / "cases").glob("*/current.json"):
                self.index_case(json.loads(p.read_text(encoding="utf-8")))

    def close(self):
        self.db.close()
