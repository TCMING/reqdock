"""Import data only. No remote refs, scripts, HTTP calls or model execution."""

import hashlib
import hmac
import ipaddress
import json
import os
import re
from collections import Counter
from urllib.parse import urlsplit, urlunsplit, urlencode, parse_qsl
from uuid import uuid4, uuid5, NAMESPACE_URL
from .private_data import transform
from .security import sensitive
from .network import origin, validate_environment, redact_url
from .storage import atomic, now

METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


class Importer:
    def __init__(self, store, vault, port):
        self.store = store
        self.vault = vault
        self.port = port
        self.master = vault.get("local_master")
        self.secrets = []
        self.secret_refs = set()

    def digest(self, value):
        return hmac.new(
            self.master.encode(),
            json.dumps(value, sort_keys=True, ensure_ascii=False).encode(),
            hashlib.sha256,
        ).hexdigest()

    def secret(self, value):
        if not isinstance(value, str) or not value or "{{" in value:
            return value
        if len(value) > 4096:
            raise ValueError("源配置中的秘密超过系统凭证存储上限，未明文导入")
        id = str(uuid5(NAMESPACE_URL, "api-post-import-secret:" + self.digest(value)))
        if not self.vault.get("import:" + id):
            self.vault.set("import:" + id, value)
        self.secrets.append(value)
        self.secret_refs.add(id)
        return "{{secret:" + id + "}}"

    def clean(self, value):
        if isinstance(value, dict):
            result = {}
            for k, v in value.items():
                if sensitive(k) and isinstance(v, str):
                    result[k] = self.secret(v)
                elif sensitive(k) and isinstance(v, dict):
                    result[k] = {
                        field: self.secret(item)
                        if field in {"example", "default"} and isinstance(item, str)
                        else self.clean(item)
                        for field, item in v.items()
                    }
                else:
                    result[k] = self.clean(v)
            return result
        if isinstance(value, list):
            return [self.clean(x) for x in value]
        return value

    def body(self, value):
        text = (
            value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, indent=2)
        )
        try:
            return json.dumps(
                self.clean(json.loads(text)), ensure_ascii=False, indent=2
            ), True
        except (ValueError, RecursionError):

            def replace(match):
                key = match.group(1)
                if not sensitive(key):
                    return match.group(0)
                try:
                    secret = json.loads('"' + match.group(2) + '"')
                except ValueError:
                    return match.group(0)
                return json.dumps(key) + ": " + json.dumps(self.secret(secret))

            return re.sub(
                r'"([^"\\]+)"\s*:\s*"((?:\\.|[^"\\])*)"', replace, text
            ), False

    def url(self, value):
        p = urlsplit(value)
        query = [
            (k, self.secret(v) if sensitive(k) else v)
            for k, v in parse_qsl(p.query, keep_blank_values=True)
        ]
        return urlunsplit(
            (p.scheme, p.netloc, p.path, urlencode(query, safe="{}:"), p.fragment)
        )

    def discover(self, value, depth=0):
        if depth > 64:
            raise ValueError("导入结构嵌套过深")
        if isinstance(value, dict):
            if sensitive(value.get("name", "")) and isinstance(
                value.get("example"), str
            ):
                self.secret(value["example"])
            for key, item in value.items():
                if sensitive(key) and isinstance(item, str):
                    self.secret(item)
                if (
                    key == "example"
                    and isinstance(item, str)
                    and item.lstrip().startswith(("{", "["))
                ):
                    self.body(item)
                if isinstance(key, str) and "?" in key and "/" in key:
                    self.url(key)
                self.discover(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                self.discover(item, depth + 1)

    def private_template(self, text):
        for secret in sorted(set(self.secrets), key=len, reverse=True):
            if secret and len(secret) >= 8 and secret in text:
                parts = re.split(r"(\{\{secret:[0-9a-f-]{36}\}\})", text)
                text = "".join(
                    part
                    if re.fullmatch(r"\{\{secret:[0-9a-f-]{36}\}\}", part)
                    else part.replace(secret, self.secret(secret))
                    for part in parts
                )
        return text

    def private_body(self, text):
        def protect(value):
            if isinstance(value, dict):
                return {k: protect(v) for k, v in value.items()}
            if isinstance(value, list):
                return [protect(v) for v in value]
            return self.private_template(value) if isinstance(value, str) else value

        try:
            return json.dumps(protect(json.loads(text)), ensure_ascii=False, indent=2)
        except (ValueError, RecursionError):
            return self.private_template(text)

    def repair_prior_import(self, source_id):
        repaired = 0
        for current in (self.store.root / "cases").glob("*/current.json"):
            value = json.loads(current.read_text(encoding="utf-8"))
            if not value.get("source", {}).get("key", "").startswith(source_id + "/"):
                continue
            for path in [
                current,
                *sorted((current.parent / "revisions").glob("*.json")),
            ]:
                value = json.loads(path.read_text(encoding="utf-8"))
                request = value.get("request", {})
                changed = False
                for key in ["body", "path"]:
                    if isinstance(request.get(key), str):
                        cleaned = (
                            self.private_body(request[key])
                            if key == "body"
                            else self.private_template(request[key])
                        )
                        changed |= cleaned != request[key]
                        request[key] = cleaned
                for key, item in request.get("headers", {}).items():
                    cleaned = self.private_template(item)
                    changed |= cleaned != item
                    request["headers"][key] = cleaned
                if changed:
                    value["security_migration"] = 1
                    atomic(path, value)
                    repaired += 1
                    if path == current:
                        self.store.index_case(value)
        return repaired

    def environment(
        self, name, url, source_key, variables, stage, region, group_label=None
    ):
        scheme, host, port = origin(url)
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = host == "localhost"
        scope = (
            "local"
            if local
            else "internal"
            if scheme == "http" or host.endswith((".domain", ".qae", ".storage"))
            else "public"
        )
        data = {
            "name": name[:80],
            "base_url": url,
            "region": "local" if local else region,
            "stage": "local" if local else stage,
            "network_scope": scope,
            "credential_ref": None,
            "variables": variables,
            "read_timeout": 120,
            "total_timeout": 600,
        }
        validate_environment(data, self.port)
        digest = self.digest(data)
        existing = next(
            (
                x
                for x in self.store.envs(include_archived=True)
                if x.get("source", {}).get("key") == source_key
            ),
            None,
        )
        if existing and existing["source"]["digest"] == digest:
            return existing
        if existing and existing["revision"] != existing["source"].get(
            "import_revision"
        ):
            return existing
        revision = existing["revision"] + 1 if existing else 1
        data["source"] = {
            "key": source_key,
            "digest": digest,
            "import_revision": revision,
            **({"group_label": group_label} if group_label else {}),
        }
        return self.store.save_env(
            data,
            existing["id"] if existing else None,
            existing["revision"] if existing else None,
        )

    def run(self, document, source_id):
        self.pending_report = None
        try:
            return self._run(document, source_id)
        except Exception as exc:
            if self.pending_report:
                folder, report = self.pending_report
                report["status"] = "failed"
                report["error_type"] = type(exc).__name__
                atomic(folder / "report.json", report)
            raise

    def _run(self, document, source_id):
        if not str(document.get("openapi", "")).startswith("3."):
            raise ValueError("仅支持 OpenAPI 3 文档")
        paths = document.get("paths")
        servers = document.get("servers", [])
        if (
            not isinstance(paths, dict)
            or len(paths) > 3000
            or not isinstance(servers, list)
            or len(servers) > 128
        ):
            raise ValueError("文档结构或数量超限")
        source_id = "openapi:" + source_id
        title = str(document.get("info", {}).get("title", "导入项目"))[:80]
        import_id = str(uuid4())
        folder = self.store.path("imports", import_id)
        folder.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(document, ensure_ascii=False).encode()
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("导入文件超过 8 MiB")
        sealed = transform(raw)
        temporary = folder / "source.tmp"
        temporary.write_bytes(sealed)
        os.replace(temporary, folder / "source.dpapi")
        report = {
            "schema_version": 1,
            "id": import_id,
            "source_id": source_id,
            "title": title,
            "imported_at": now(),
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "source_protection": "Windows current-user DPAPI",
            "source_servers": len(servers),
            "operations": 0,
            "cases": dict(Counter()),
            "environment_ids": [],
            "missing_environments": [],
            "limitations": [
                "OpenAPI 未提供 ApiPost 预执行脚本、Cookie 会话与历史测试记录；不执行外部引用或脚本。"
            ],
            "warning_counts": {},
            "secret_count": 0,
        }
        report["status"] = "running"
        self.pending_report = (folder, report)
        variables_to_env = {}
        fixed_env = {}
        warning_counts = Counter()
        actions = Counter()
        report["cases"] = actions
        # Discover secrets across all operations before writing any normalized case.
        self.discover(document)
        report["security_repaired_revisions"] = self.repair_prior_import(source_id)
        for index, server in enumerate(servers):
            variables = server.get("variables", {})
            if not isinstance(variables, dict) or len(variables) > 128:
                raise ValueError("环境变量数量超限")
            values = {
                k: str(v.get("default", ""))
                for k, v in variables.items()
                if isinstance(v, dict)
            }
            label = str(server.get("description") or "环境 " + str(index + 1))
            stage = (
                "test"
                if any(x in label for x in ["测试", "灰度", "test", "gray"])
                else "prod"
            )
            region = (
                "international" if "海外" in label or "国际" in label else "domestic"
            )
            targets = values or {"base_url": server.get("url", "")}
            for key, value in targets.items():
                if not value or not value.startswith(("http://", "https://")):
                    report["missing_environments"].append(
                        {
                            "group": label,
                            "variable": key,
                            "reason": "没有可用基础地址，保留在加密源文件",
                        }
                    )
                    continue
                try:
                    env = self.environment(
                        title + " / " + label + " / " + key,
                        value,
                        source_id + "/server/" + str(index) + "/" + key,
                        self.clean(values),
                        stage,
                        region,
                        label,
                    )
                    variables_to_env.setdefault(key, []).append(env["id"])
                    report["environment_ids"].append(env["id"])
                except ValueError:
                    report["missing_environments"].append(
                        {
                            "group": label,
                            "variable": key,
                            "reason": "地址策略校验未通过，未启用",
                        }
                    )
        for original_path, item in paths.items():
            path = original_path.strip()
            if not isinstance(item, dict):
                continue
            for method, operation in item.items():
                if method not in METHODS or not isinstance(operation, dict):
                    continue
                report["operations"] += 1
                if report["operations"] > 4000:
                    raise ValueError("接口数量超限")
                warnings = []
                if original_path != path:
                    warnings.append("URL 首尾空白已去除，原文保存在加密源文件")
                blocking = []
                parameters = item.get("parameters", []) + operation.get(
                    "parameters", []
                )
                headers = {}
                query = []
                materialized_path = path
                for parameter in parameters:
                    if "$ref" in parameter:
                        warnings.append("未解析参数引用")
                        continue
                    value = parameter.get(
                        "example", parameter.get("schema", {}).get("default", "")
                    )
                    key = str(parameter.get("name", ""))
                    if sensitive(key) and isinstance(value, str):
                        value = self.secret(value)
                    elif not isinstance(value, str):
                        value = json.dumps(value, ensure_ascii=False)
                    where = parameter.get("in")
                    if where == "header":
                        headers[key] = value
                    elif where == "query":
                        query.append((key, value))
                    elif where == "path":
                        if value:
                            materialized_path = materialized_path.replace(
                                "{" + key + "}", value
                            )
                        else:
                            warnings.append("路径参数需要填写")
                if query:
                    materialized_path += (
                        "&" if "?" in materialized_path else "?"
                    ) + urlencode(query, safe="{}:")
                materialized_path = self.url(materialized_path)
                match = re.match(r"^\{\{([^{}]+)\}\}", path)
                variable = match.group(1) if match else None
                environments = variables_to_env.get(variable, []) if variable else []
                if not variable and path.startswith(("http://", "https://")):
                    scheme, host, port = origin(path)
                    authority = urlsplit(path).netloc
                    base = scheme + "://" + authority
                    if base not in fixed_env:
                        try:
                            env = self.environment(
                                title + " / 固定地址 / " + authority,
                                base,
                                source_id + "/fixed/" + base,
                                {},
                                "test"
                                if any(x in host for x in ["test", "gray"])
                                else "prod",
                                "international" if "intl" in host else "domestic",
                            )
                            fixed_env[base] = env["id"]
                            report["environment_ids"].append(env["id"])
                        except ValueError:
                            fixed_env[base] = None
                    if fixed_env[base]:
                        environments = [fixed_env[base]]
                if not environments:
                    warnings.append("需要手动选择或补充环境")
                content = operation.get("requestBody", {}).get("content", {})
                body = ""
                body_type = "none"
                stream = False
                if content:
                    media = (
                        "application/json"
                        if "application/json" in content
                        else next(iter(content))
                    )
                    definition = content[media]
                    example = definition.get("example")
                    if example is None:
                        examples = definition.get("examples", {})
                        example = next(
                            (
                                x.get("value")
                                for x in examples.values()
                                if isinstance(x, dict) and "value" in x
                            ),
                            None,
                        )
                    if media == "application/json":
                        if example is None:
                            example = {}
                            warnings.append("正文为生成草稿，源文件未提供示例")
                        body, valid = self.body(example)
                        body_type = "json"
                        if not valid:
                            warnings.append("JSON 示例需修正后发送")
                        try:
                            stream = json.loads(body).get("stream") is True
                        except (ValueError, AttributeError):
                            pass
                        headers.setdefault("Content-Type", media)
                    elif media == "application/x-www-form-urlencoded":
                        properties = definition.get("schema", {}).get("properties", {})
                        values = (
                            example
                            if isinstance(example, dict)
                            else {
                                k: v.get("example", v.get("default", ""))
                                for k, v in properties.items()
                            }
                        )
                        body = urlencode(self.clean(values), doseq=True, safe="{}:")
                        body_type = "text"
                        headers.setdefault("Content-Type", media)
                        warnings.append("表单字段已转 URL 编码文本，请核对空字段")
                    else:
                        body = json.dumps(
                            self.clean(definition), ensure_ascii=False, indent=2
                        )
                        body_type = "text"
                        blocking.append("暂不支持 " + media + " 的直接发送")
                        warnings.append("已保留表单定义，仅可查看")
                tags = operation.get("tags") or ["未分组"]
                groups = list(dict.fromkeys(str(t) for t in tags))
                group = groups[0]
                request = {
                    "name": str(operation.get("summary") or path)[:120],
                    "method": method.upper(),
                    "path": materialized_path,
                    "headers": headers,
                    "body": body,
                    "body_type": body_type,
                    "stream": stream,
                    "expected_status": None,
                    "contains": "",
                    "entry_type": "qizhi"
                    if "/prompt/" in path
                    else "ibrain"
                    if variable == "ibrain-common"
                    else "aibots"
                    if variable and "aibots" in variable
                    else "raw",
                }
                request["body"] = self.private_body(request["body"])
                request["path"] = self.private_template(request["path"])
                request["headers"] = {
                    k: self.private_template(v) for k, v in request["headers"].items()
                }
                source_key = (
                    source_id
                    + "/operation/"
                    + str(operation.get("x-target-id") or path)
                    + "/"
                    + method
                )
                value = {
                    "name": request["name"],
                    "group": group,
                    "groups": groups,
                    "method": method.upper(),
                    "path_preview": redact_url(path),
                    "server_variable": variable,
                    "environment_ids": environments,
                    "warnings": warnings,
                    "blocking": blocking,
                    "request": request,
                    "source": {
                        "key": source_key,
                        "digest": self.digest({"operation": operation, "path": path}),
                        "project": title,
                        "target_id": operation.get("x-target-id"),
                        "format": "OpenAPI 3",
                    },
                }
                _, action = self.store.save_imported_case(value)
                actions[action] += 1
                warning_counts.update(warnings)
        report["cases"] = dict(actions)
        report["environment_ids"] = list(dict.fromkeys(report["environment_ids"]))
        report["secret_count"] = len(self.secret_refs)
        report["warning_counts"] = dict(warning_counts)
        report["status"] = "completed"
        atomic(folder / "report.json", report)
        return report
