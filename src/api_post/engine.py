import asyncio
import hashlib
import json
import shutil
import time
import re
from contextlib import asynccontextmanager
from uuid import UUID
from urllib.parse import urlsplit, parse_qsl
import httpx
import httpcore
import psutil
from httpx_sse import EventSource
from .network import target_url, validate_environment, redact_url, PinnedTransport
from .security import redact, safe_text, sensitive
from .storage import now
from .response_media import ResponseMedia

MAX_BODY = 2 * 1024 * 1024
MAX_EVENT = 2 * 1024 * 1024
MAX_STREAM = 50 * 1024 * 1024


def saved_header_items(headers, known):
    return [
        {
            "name": name,
            "value": "[REDACTED]" if sensitive(name) else redact(value, known),
        }
        for name, value in headers.multi_items()
    ]


@asynccontextmanager
async def open_response(client, request):
    response = await client.send(request, stream=True)
    try:
        yield response
    finally:
        await response.aclose()


class LimitError(ValueError):
    pass


class GuardedStream(httpx.AsyncByteStream):
    def __init__(self, inner):
        self.inner = inner
        self.total = 0
        self.event_size = 0
        self.line_size = 0
        self.after_cr = False

    async def __aiter__(self):
        async for chunk in self.inner:
            self.total += len(chunk)
            if self.total > MAX_STREAM:
                raise LimitError("流响应超过 50 MiB 上限")
            for b in chunk:
                if b == 10 and self.after_cr:
                    self.after_cr = False
                    continue
                self.after_cr = b == 13
                self.event_size += 1
                if self.event_size > MAX_EVENT:
                    raise LimitError("单个 SSE 事件超过 2 MiB 上限")
                if b in (10, 13):
                    if self.line_size == 0:
                        self.event_size = 0
                    self.line_size = 0
                else:
                    self.line_size += 1
            yield chunk

    async def aclose(self):
        await self.inner.aclose()


class Engine:
    def __init__(self, store, vault, port, resources=None):
        self.store = store
        self.vault = vault
        self.port = port
        self.resources = resources
        self.tasks = {}
        self.slot = asyncio.Semaphore(1)
        self.submission_lock = asyncio.Lock()

    def admission(self):
        if self.resources:
            self.resources.capacity()
        if len(self.tasks) >= 6:
            raise LimitError("运行队列已满（1 个执行 + 5 个等待）")
        if psutil.Process().memory_info().private > 350 * 1024 * 1024:
            raise LimitError("内存超过准入阈值，请稍后重试")
        if shutil.disk_usage(self.store.root).free < 1024**3:
            raise LimitError("磁盘可用空间不足 1 GiB")
        # Aggregate evidence accounting comes from indexed manifests, not scanning large bodies.
        rows = self.store.db.execute(
            "SELECT COALESCE(SUM(COALESCE(json_extract(summary,'$.evidence_bytes'),0)+COALESCE(json_extract(summary,'$.snapshot_bytes'),0)),0) FROM runs"
        ).fetchone()
        if rows[0] > 25 * 1024**3:
            raise LimitError("工作区证据达到容量上限")

    def prepare(self, data, env, known=None):
        if env.get("archived"):
            raise ValueError("此服务变量已移除，请重新选择环境和服务变量")
        known = [] if known is None else known
        validate_environment(env, self.port)
        variables = dict(env.get("variables", {}))
        variables.setdefault("base_url", env["base_url"])
        leading = re.match(r"^\{\{([^{}:]+)\}\}", data["path"])
        if leading and leading.group(1) not in variables:
            source = env.get("source", {}).get("key", "")
            assigned = env.get("grouping", {})
            if ("/server/" in source or assigned) and not (
                source.endswith("/base_url") or assigned.get("variable") == "base_url"
            ):
                raise ValueError("请求使用的服务变量在当前环境中不存在，请核对后发送")
            variables[leading.group(1)] = env["base_url"]

        def secret_value(key):
            try:
                id = str(UUID(key[7:]))
            except ValueError:
                raise ValueError("秘密引用不合法") from None
            secret = self.vault.get("import:" + id)
            if secret is None:
                raise ValueError("变量或导入凭证缺失，请重新设置")
            known.append(secret)
            return secret

        def render(value):
            if isinstance(value, dict):
                return {k: render(v) for k, v in value.items()}
            if isinstance(value, list):
                return [render(v) for v in value]
            if not isinstance(value, str):
                return value

            def replace(match):
                key = match.group(1)
                if key.startswith("secret:"):
                    return secret_value(key)
                if key not in variables:
                    raise ValueError("请求含未设置的变量，请补充后发送")
                # Variables cannot introduce scripts or recursive substitutions.
                variable = variables[key]
                if "{{" in re.sub(r"\{\{secret:[0-9a-f-]{36}\}\}", "", variable):
                    raise ValueError("不支持嵌套环境变量")
                variable = re.sub(
                    r"\{\{(secret:[0-9a-f-]{36})\}\}",
                    lambda m: secret_value(m.group(1)),
                    variable,
                )
                if sensitive(key) or key in env.get("secret_variables", []):
                    known.append(variable)
                return variable

            return re.sub(r"\{\{([^{}]+)\}\}", replace, value)

        path = render(data["path"])
        if re.search(r"\{[^{}]+\}", urlsplit(path).path):
            raise ValueError("请先填写路径参数")
        url = target_url(env, path)
        headers = {}
        forbidden = {
            "host",
            "content-length",
            "connection",
            "transfer-encoding",
            "upgrade",
            "proxy-connection",
            "accept-encoding",
            "expect",
        }
        for k, v in data["headers"].items():
            if (
                len(k) > 100
                or len(v) > 4096
                or any(c in k + v for c in "\r\n")
                or not k
                or not all(
                    c.isascii() and (c.isalnum() or c in "!#$%&'*+-.^_`|~") for c in k
                )
            ):
                raise ValueError("请求头格式不合法")
            if k.lower() in forbidden:
                continue
            if sensitive(k) and not re.fullmatch(
                r"(?:(?:Bearer|Basic) )?\{\{[^{}]+\}\}", v, re.IGNORECASE
            ):
                raise ValueError("敏感请求头请通过环境凭证配置，不直接写入请求")
            headers[k] = render(v)
            if sensitive(k):
                known.append(headers[k])
            if any(c in headers[k] for c in "\r\n"):
                raise ValueError("变量产生了不合法请求头")
        body = data["body"] if data["body_type"] != "none" else ""
        if data["body_type"] == "json":
            try:
                parsed = json.loads(body)
            except (ValueError, RecursionError):
                raise ValueError("JSON 请求正文格式不正确")
            if data.get("resource_bindings"):
                parsed, snapshots = self.resources.resolve(
                    parsed, data["resource_bindings"], env, known
                )
                data["_resource_snapshots"] = snapshots
            body = json.dumps(render(parsed), ensure_ascii=False)
            headers.setdefault("Content-Type", "application/json")
        elif data["body_type"] == "text":
            body = render(body)
            headers.setdefault("Content-Type", "text/plain; charset=utf-8")
        if data.get("resource_bindings") and data["body_type"] != "json":
            raise ValueError("资源绑定需要 JSON 正文")
        if len(body.encode()) > 8 * 1024 * 1024:
            raise ValueError("请求正文超过 8 MiB")
        headers["Accept-Encoding"] = "identity"
        if data["stream"]:
            headers.setdefault("Accept", "text/event-stream")
        # Snapshot redacts structured body fields and secret query values.
        return url, headers, body

    async def submit(self, model):
        async with self.submission_lock:
            return await self._submit(model)

    async def _submit(self, model):
        data = model.model_dump(mode="json")
        env = await asyncio.to_thread(self.store.env, data["environment_id"])
        if data.get("case_id"):
            case = await asyncio.to_thread(self.store.case, data["case_id"])
            if case.get("blocking"):
                raise ValueError("此导入用例的表单协议暂不支持，不能直接发送")
        known = []
        url, headers, body = await asyncio.to_thread(self.prepare, data, env, known)
        known.extend(v for k, v in parse_qsl(urlsplit(url).query) if sensitive(k))

        def collect(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if sensitive(key) and isinstance(item, str):
                        known.append(item)
                    else:
                        collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)

        if data["body_type"] == "json":
            collect(json.loads(body))
        if env.get("credential_ref"):
            secret = await asyncio.to_thread(
                self.vault.get, "credential:" + env["credential_ref"]
            )
            if not secret:
                raise ValueError("环境凭证缺失或已撤销")
            known.append(secret)
        digest = hashlib.sha256(
            json.dumps(
                {"data": data, "environment_revision": env["revision"]}, sort_keys=True
            ).encode()
        ).hexdigest()
        # Check idempotency before admission; task creation is serialized by the event loop.
        row = self.store.db.execute(
            "SELECT id,digest FROM runs WHERE submission_key=?",
            (data["submission_key"],),
        ).fetchone()
        if row:
            if row[1] != digest:
                raise ValueError("相同提交标识对应不同请求")
            return await asyncio.to_thread(self.store.run, row[0])
        self.admission()
        context = {**redact(data["execution_context"], known), "source": data["execution_context"]["source"]}
        snapshot = {
            "execution_context": context,
            "name": data["name"],
            "environment_id": env["id"],
            "environment_name": env["name"],
            "method": data["method"],
            "entry_type": data["entry_type"],
            "url": redact_url(url),
            "environment": env,
            "request": {
                **data,
                "execution_context": context,
                "path": redact_url(data["path"]),
                "headers": redact(headers, known),
                "configured_headers": redact(headers, known),
                "headers_source": "configured",
                "body": safe_text(body, known),
                "contains": redact(data["contains"], known),
                "resolved_url": redact_url(url),
            },
            "stream": data["stream"],
            "evidence_bytes": 0,
            "resource_snapshots": data.get("_resource_snapshots", []),
        }
        record, created = await asyncio.to_thread(
            self.store.create_run, snapshot, data["submission_key"], digest
        )
        if created:
            task = asyncio.create_task(
                self.execute(record, data, env, url, headers, body, known)
            )
            self.tasks[record["id"]] = task
            task.add_done_callback(lambda t: self.tasks.pop(record["id"], None))
        return record

    async def execute(self, record, data, env, url, headers, body, known):
        start = None
        content_tail = ""
        discovered = ResponseMedia()
        contains_found = False
        event_path = self.store.path("runs", record["id"]) / "stream.events.ndjson"
        try:
            async with self.slot:
                if env.get("credential_ref"):
                    meta = next(
                        (
                            x
                            for x in self.store.credential_meta()
                            if x["id"] == env["credential_ref"]
                        ),
                        None,
                    )
                    secret = await asyncio.to_thread(
                        self.vault.get, "credential:" + env["credential_ref"]
                    )
                    if not meta or not secret:
                        raise ValueError("环境凭证缺失或已撤销")
                    headers[meta["header"]] = meta["prefix"] + secret
                    known = known + [secret, meta["prefix"] + secret]
                start = time.monotonic()
                record.update(state="running", started_at=now())
                await asyncio.to_thread(self.store.update_run, record)
                timeout = httpx.Timeout(
                    connect=10, read=env["read_timeout"], write=60, pool=5
                )
                async with asyncio.timeout(env["total_timeout"]):
                    async with httpx.AsyncClient(
                        transport=PinnedTransport(env, self.port),
                        timeout=timeout,
                        trust_env=False,
                        follow_redirects=False,
                    ) as client:
                        request = client.build_request(
                            data["method"], url, headers=headers, content=body.encode()
                        )
                        record["request"].update(
                            headers=redact(dict(request.headers), known),
                            header_items=saved_header_items(request.headers, known),
                            headers_source="final_pre_send",
                        )
                        await asyncio.to_thread(self.store.update_run, record)
                        async with open_response(client, request) as response:
                            record.update(
                                status_code=response.status_code,
                                response_headers=redact(dict(response.headers), known),
                                response_header_items=saved_header_items(
                                    response.headers, known
                                ),
                                headers_ms=round((time.monotonic() - start) * 1000, 2),
                                is_sse="text/event-stream"
                                in response.headers.get("content-type", "").lower(),
                            )
                            encoding = response.headers.get(
                                "content-encoding", "identity"
                            ).lower()
                            if encoding not in {"", "identity"}:
                                raise LimitError(
                                    "本阶段不接收压缩响应，请服务端返回 identity"
                                )
                            await asyncio.to_thread(self.store.update_run, record)
                            if record["is_sse"]:
                                response.stream = GuardedStream(response.stream)
                                record["stream_end"] = "unknown"
                                count = 0
                                with event_path.open("ab") as f:
                                    async for event in EventSource(
                                        response
                                    ).aiter_sse():
                                        count += 1
                                        elapsed = round(
                                            (time.monotonic() - start) * 1000, 2
                                        )
                                        record.setdefault("first_event_ms", elapsed)
                                        if event.event != "error" and event.data != "[DONE]":
                                            discovered.text(event.data)
                                            discovered.protect(known)
                                        safe = safe_text(event.data, known)
                                        item = {
                                            "sequence": count,
                                            "event": redact(event.event, known),
                                            "data": safe,
                                            "elapsed_ms": elapsed,
                                        }
                                        line = (
                                            json.dumps(item, ensure_ascii=False) + "\n"
                                        ).encode()
                                        if len(line) > MAX_EVENT * 2:
                                            raise LimitError("事件持久化上限")
                                        if (
                                            record["evidence_bytes"] + len(line)
                                            > 128 * 1024 * 1024
                                        ):
                                            raise LimitError("证据总量超限")
                                        f.write(line)
                                        f.flush()
                                        record["evidence_bytes"] += len(line)
                                        if event.event == "error":
                                            record["stream_error"] = True
                                        if event.data == "[DONE]":
                                            record["stream_end"] = "done_marker"
                                            break
                                        text = ""
                                        try:
                                            parsed = json.loads(safe)
                                            if isinstance(parsed, dict):
                                                if parsed.get("error"):
                                                    record["stream_error"] = True
                                                for choice in parsed.get("choices", []):
                                                    if choice.get("finish_reason"):
                                                        record["stream_end"] = (
                                                            "finish_reason"
                                                        )
                                                    text += (
                                                        choice.get("delta", {}).get(
                                                            "content"
                                                        )
                                                        or ""
                                                    )
                                        except (ValueError, TypeError, AttributeError):
                                            pass
                                        if text:
                                            record.setdefault(
                                                "first_content_ms", elapsed
                                            )
                                            combined = content_tail + text
                                            contains_found = contains_found or bool(
                                                data["contains"]
                                                and data["contains"] in combined
                                            )
                                            content_tail = combined[-262144:]
                                        if count % 10 == 0:
                                            await asyncio.to_thread(
                                                self.store.update_run, record
                                            )
                                record["events_count"] = count
                                if data["stream"] and record["stream_end"] == "unknown":
                                    record["stream_warning"] = (
                                        "未发现 OpenAI 终止标记；通用 SSE 的 EOF 不证明业务成功"
                                    )
                            else:
                                buf = bytearray()
                                oversized = False
                                async for chunk in response.aiter_bytes(
                                    chunk_size=16384
                                ):
                                    if len(buf) + len(chunk) > MAX_BODY:
                                        record["truncated"] = True
                                        oversized = True
                                        break
                                    buf.extend(chunk)
                                text = bytes(buf).decode(
                                    response.encoding or "utf-8", errors="replace"
                                )
                                if not oversized:
                                    media_kind = response.headers.get("content-type", "").split("/")[0].lower()
                                    if media_kind in {"image", "video", "audio"}:
                                        discovered.add(media_kind, content=bytes(buf))
                                    else:
                                        discovered.text(text)
                                    discovered.protect(known)
                                content_tail = safe_text(text, known)
                                destination = (
                                    self.store.path("runs", record["id"])
                                    / "response.body.txt"
                                )
                                destination.write_text(content_tail, encoding="utf-8")
                                record["evidence_bytes"] = destination.stat().st_size
                                if oversized:
                                    raise LimitError(
                                        "当前阶段普通响应上限 2 MiB，已保存前缀并中止读取"
                                    )
                                if data["stream"]:
                                    record["stream_warning"] = (
                                        "请求预期 SSE，但响应类型不是 text/event-stream"
                                    )
                checks = []
                if data["expected_status"] is not None:
                    checks.append(
                        {
                            "name": "HTTP 状态",
                            "passed": record.get("status_code")
                            == data["expected_status"],
                        }
                    )
                if data["contains"]:
                    checks.append(
                        {
                            "name": "内容包含",
                            "passed": contains_found
                            or data["contains"] in content_tail,
                        }
                    )
                if data["stream"]:
                    checks.append(
                        {
                            "name": "OpenAI 流正常结束",
                            "passed": record.get("is_sse")
                            and record.get("stream_end")
                            in {"done_marker", "finish_reason"}
                            and not record.get("stream_error"),
                        }
                    )
                record.update(
                    state="completed",
                    checks=checks,
                    validation_result=(
                        "pass" if all(x["passed"] for x in checks) else "fail"
                    )
                    if checks
                    else "unknown",
                )
        except asyncio.CancelledError:
            record.update(state="cancelled", error="已取消本地请求；上游是否停止未知")
        except (TimeoutError, httpx.TimeoutException, httpcore.TimeoutException):
            record.update(state="timed_out", error="请求超时；未自动重试")
        except (ValueError, LimitError) as exc:
            record.update(state="failed", error=redact(str(exc), known)[:256])
        except Exception as exc:
            record.update(state="failed", error="请求执行失败：" + type(exc).__name__)
        finally:
            if self.resources and discovered.items and 200 <= record.get("status_code", 0) < 300:
                try:
                    captured, failures = await asyncio.to_thread(self.resources.capture_response, record["id"], discovered)
                    record["response_resources"] = captured
                    if failures or discovered.limited:
                        record["resource_capture_warning"] = "部分响应资源未入库：格式、容量或识别数量限制；原请求结果已保留"
                except Exception:
                    record["resource_capture_warning"] = "响应资源入库未完成；原请求结果已保留"
            record.update(
                ended_at=now(),
                total_ms=round((time.monotonic() - start) * 1000, 2) if start else None,
            )
            if event_path.exists():
                record["evidence_bytes"] = event_path.stat().st_size
            await asyncio.to_thread(self.store.update_run, record)

    async def cancel(self, id):
        record = await asyncio.to_thread(self.store.run, id)
        task = self.tasks.get(id)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                record.update(
                    state="cancelled", error="排队任务已取消，未发送", ended_at=now()
                )
                await asyncio.to_thread(self.store.update_run, record)
        return await asyncio.to_thread(self.store.run, id)

    async def close(self):
        ids = list(self.tasks)
        for id in ids:
            await self.cancel(id)
