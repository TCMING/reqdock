import asyncio
import hmac
import hashlib
import ipaddress
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from .sql_workspace import SQLWorkspace, router as sql_router
from .engine import Engine, LimitError
from .models import (
    EnvironmentInput,
    EnvironmentUpdate,
    EnvironmentGroupUpdate,
    EnvironmentGroupArchive,
    RunInput,
    CredentialInput,
    PairInput,
    OpenAPIImport,
    ResourceInput,
    ResourceDownload,
    CaseSave,
    ResponseExampleSave,
    TaxonomyCommand,
    CaseClassification,
)
from .network import validate_environment
from .security import Auth, Vault, sensitive
from .storage import Store, now
from .openapi_import import Importer
from .resources import ResourceManager
from .environment_groups import EnvironmentGroups, protect_variables


class AccessControl:
    def __init__(self, app, auth, port, access_mode="paired"):
        self.app = app
        self.auth = auth
        self.port = port
        self.access_mode = access_mode

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        host = request.headers.get("host", "")
        allowed = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

        async def deny(code, message):
            await JSONResponse({"detail": message}, status_code=code)(
                scope, receive, send
            )

        local_only = self.access_mode == "local"
        if local_only:
            try:
                peer = scope.get("client")
                if not peer or not ipaddress.ip_address(peer[0]).is_loopback:
                    return await deny(403, "仅允许本机回环访问")
            except ValueError:
                return await deny(403, "仅允许本机回环访问")
        if host not in allowed:
            return await deny(403, "无效 Host")
        origin = request.headers.get("origin")
        if origin and origin not in {"http://" + h for h in allowed}:
            return await deny(403, "拒绝跨站访问")
        if request.headers.get("sec-fetch-site") == "cross-site":
            return await deny(403, "拒绝跨站访问")
        path = request.url.path
        bearer = not local_only and self.auth.bearer(request)
        session = self.auth.session(request) if not local_only else None
        public = path in {
            "/",
            "/api/health",
            "/api/auth/pair",
            "/favicon.svg",
        } or path.startswith("/assets/")
        if not local_only and not public and not bearer and not session:
            return await deny(401, "请先配对本机服务")
        if local_only:
            # Authentication is intentionally disabled for this local workspace.
            # Keep browser-origin defenses; no secret or pairing is required.
            fetch_site = request.headers.get("sec-fetch-site")
            if fetch_site and fetch_site not in {"none", "same-origin"}:
                return await deny(403, "拒绝跨站访问")
            if request.method not in {"GET", "HEAD", "OPTIONS"} and not origin:
                if any(request.headers.get(h) for h in ("sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest")):
                    return await deny(403, "浏览器写操作缺少可信 Origin")
                # Native callers can use ordinary JSON without access tokens.
                # Simple HTML forms cannot satisfy this media-type requirement.
                if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json" and request.headers.get("content-length", "0") != "0":
                    return await deny(403, "无Origin的本机写请求须使用application/json")
        elif request.method not in {"GET", "HEAD", "OPTIONS"}:
            if not bearer:
                if origin not in {"http://" + h for h in allowed}:
                    return await deny(403, "缺少可信 Origin")
                if path != "/api/auth/pair" and (
                    not session
                    or not hmac.compare_digest(
                        request.headers.get("x-csrf-token", ""), self.auth.csrf(session)
                    )
                ):
                    return await deny(403, "CSRF 校验失败")
        try:
            length = int(request.headers.get("content-length", "0"))
        except ValueError:
            return await deny(400, "无效长度")
        body_limit = (
            2 * 1024**3
            if request.method == "POST"
            and re.fullmatch(r"/api/resources/[0-9a-f-]{36}/file", path)
            else 10 * 1024**2
        )
        if length > body_limit or length < 0:
            return await deny(413, "本地 API 请求过大")
        count = 0

        async def limited_receive():
            nonlocal count
            message = await receive()
            count += len(message.get("body", b""))
            if count > body_limit:
                raise HTTPException(413, "本地 API 请求过大")
            return message

        async def secure_send(message):
            if message["type"] == "http.response.start":
                message["headers"] += [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"cache-control", b"no-store"),
                    (
                        b"content-security-policy",
                        b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                    ),
                ]
            await send(message)

        await self.app(scope, limited_receive, secure_send)


def create_app(root: Path, port=18765, vault=None):
    root = Path(root).resolve()
    access_path = root / "access.json"
    access_config = json.loads(access_path.read_text(encoding="utf-8")) if access_path.exists() else {"mode": "paired"}
    access_mode = access_config.get("mode")
    if access_mode not in {"paired", "local"}:
        raise ValueError("本机访问配置mode须为paired或local")
    store = Store(root)
    vault = vault or Vault(root)
    auth = Auth(vault)
    resources = ResourceManager(store, vault, port)
    engine = Engine(store, vault, port, resources)
    environment_groups = EnvironmentGroups(store, vault, port)

    sql_workspace = SQLWorkspace(store, vault, port)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await asyncio.to_thread(sql_workspace.close)
        await resources.close()
        await engine.close()
        store.close()

    app = FastAPI(
        title="API Post Local",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.include_router(sql_router(sql_workspace))
    app.state.sql_workspace = sql_workspace
    app.state.store = store
    app.state.engine = engine
    app.state.auth = auth
    app.state.access_mode = access_mode
    app.state.resources = resources
    app.state.import_lock = asyncio.Lock()
    app.state.media_history_lock = asyncio.Lock()
    app.add_middleware(AccessControl, auth=auth, port=port, access_mode=access_mode)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse(
            {"detail": "参数不合法，请检查字段类型和长度"}, status_code=422
        )

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "记录不存在"}, status_code=404)

    @app.exception_handler(ValueError)
    async def bad(request, exc):
        return JSONResponse({"detail": str(exc)[:256]}, status_code=400)

    @app.exception_handler(LimitError)
    async def limited(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=429)

    @app.get("/api/health")
    async def health(challenge: str = ""):
        result = {
            "app": "api-post-local",
            "version": "0.1.0",
            "phase": "request-workbench",
            "access_mode": access_mode,
        }
        if re.fullmatch(r"[0-9a-f]{48}", challenge):
            result["proof"] = hmac.new(
                auth.master.encode(), challenge.encode(), hashlib.sha256
            ).hexdigest()
        return result

    @app.post("/api/auth/code")
    async def code(request: Request):
        if access_mode == "local":
            raise HTTPException(410, "本机模式已关闭配对，无需配对码")
        if not auth.bearer(request):
            raise HTTPException(403, "仅本机 CLI 可生成配对码")
        return {"code": auth.issue_code(), "expires_in": 300}

    @app.post("/api/auth/pair")
    async def pair(data: PairInput):
        if access_mode == "local":
            raise HTTPException(410, "本机模式已关闭配对，可直接访问")
        cookie = auth.exchange(data.code)
        response = JSONResponse({"ok": True})
        response.set_cookie(
            "api_post_session",
            cookie,
            max_age=auth.SESSION_SECONDS,
            httponly=True,
            samesite="strict",
        )
        return response

    @app.get("/api/auth/session")
    async def session(request: Request):
        if access_mode == "local":
            return {"csrf": "", "version": "0.1.0", "access_mode": "local"}
        cookie = auth.session(request)
        remembered = auth.remember(cookie) if cookie else None
        response = JSONResponse(
            {"csrf": auth.csrf(remembered) if remembered else "", "version": "0.1.0", "access_mode": "paired"}
        )
        if remembered and remembered != cookie:
            response.set_cookie(
                "api_post_session",
                remembered,
                max_age=auth.SESSION_SECONDS,
                httponly=True,
                samesite="strict",
            )
        return response

    @app.post("/api/auth/logout")
    async def logout(request: Request):
        cookie = auth.session(request)
        if cookie:
            await asyncio.to_thread(auth.revoke, cookie)
        response = JSONResponse({"ok": True})
        response.delete_cookie("api_post_session")
        return response

    @app.get("/api/environments")
    async def environments():
        return await asyncio.to_thread(store.envs)

    async def save_env(data, id=None, revision=None):
        value = data.model_dump(mode="json")
        value.pop("revision", None)
        old = store.env(str(id)) if id else {}
        if id and "variables" not in data.model_fields_set:
            value["variables"] = old.get("variables", {})
        if id and "secret_variables" not in data.model_fields_set:
            value["secret_variables"] = [
                k for k in old.get("secret_variables", []) if k in value["variables"]
            ]
        if any(key not in value["variables"] for key in value["secret_variables"]):
            raise ValueError("保密变量必须存在于变量列表中")
        if old and revision != old["revision"]:
            raise ValueError("环境已被修改，请刷新后再保存")
        source = old.get("source", {}).get("key", "")
        if "/server/" in source:
            bound = source.rsplit("/", 1)[-1]
            if (
                bound in value["variables"]
                and value["variables"][bound] != value["base_url"]
            ):
                raise ValueError("当前连接的地址变量须与基础地址保持一致")
        validate_environment(value, port)
        if value["credential_ref"] and not any(
            x["id"] == value["credential_ref"] for x in store.credential_meta()
        ):
            raise ValueError("凭证不存在")

        group = (
            next(
                (
                    g
                    for g in environment_groups.list()
                    if any(m["environment"]["id"] == str(id) for m in g["members"])
                ),
                None,
            )
            if id
            else None
        )
        if group and (
            group["revision"]
            or any(m["environment"].get("grouping") for m in group["members"])
        ):
            raise ValueError("该环境已按组管理，请通过编辑环境组保存")
        value["variables"] = await asyncio.to_thread(
            protect_variables,
            store,
            vault,
            port,
            value["variables"],
            value["secret_variables"],
        )
        return await asyncio.to_thread(
            store.save_env, value, str(id) if id else None, revision
        )

    @app.post("/api/environments")
    async def create_environment(data: EnvironmentInput):
        return await save_env(data)

    @app.put("/api/environments/{id}")
    async def update_environment(id: UUID, data: EnvironmentUpdate):
        return await save_env(data, id, data.revision)

    @app.get("/api/environment-groups")
    async def list_environment_groups():
        return await asyncio.to_thread(environment_groups.list)

    @app.put("/api/environment-groups/{id}")
    async def update_environment_group(id: UUID, data: EnvironmentGroupUpdate):
        return await asyncio.to_thread(environment_groups.update, str(id), data)

    @app.get("/api/credentials")
    async def credentials():
        return await asyncio.to_thread(store.credential_meta)

    @app.post("/api/environment-groups/{id}/archive")
    async def archive_environment_group(id: UUID, data: EnvironmentGroupArchive):
        return await asyncio.to_thread(environment_groups.archive, str(id), data)

    @app.post("/api/credentials")
    async def credential(data: CredentialInput):
        if data.header.lower() in {
            "host",
            "content-length",
            "connection",
            "transfer-encoding",
            "accept-encoding",
            "cookie",
        }:
            raise ValueError("不允许此凭证头")
        id = str(uuid4())
        try:
            await asyncio.to_thread(vault.set, "credential:" + id, data.value)
        except Exception:
            raise HTTPException(
                503, "系统凭证库写入失败，请检查权限及凭证长度"
            ) from None
        meta = {
            "id": id,
            "name": data.name,
            "header": data.header,
            "prefix": data.prefix,
            "created_at": now(),
        }
        await asyncio.to_thread(store.save_credential_meta, meta)
        return meta

    @app.delete("/api/credentials/{id}")
    async def remove_credential(id: UUID):
        await asyncio.to_thread(vault.delete, "credential:" + str(id))
        (store.path("credentials", id) / "current.json").unlink(missing_ok=True)
        return {"ok": True}

    @app.post("/api/runs")
    async def start_run(data: RunInput):
        return await engine.submit(data)

    @app.post("/api/import/openapi")
    async def import_openapi(data: OpenAPIImport):
        if app.state.import_lock.locked():
            raise HTTPException(429, "已有导入任务正在执行")
        async with app.state.import_lock:
            return await asyncio.to_thread(
                Importer(store, vault, port).run, data.document, data.source_id
            )

    @app.get("/api/cases")
    async def cases(
        page: int = Query(1, ge=1),
        limit: int = Query(50, ge=1, le=200),
        search: str = Query("", max_length=120),
        group: str = Query("", max_length=512),
        module: str = Query("", max_length=512),
    ):
        return await asyncio.to_thread(
            store.list_cases, page, limit, search, group, module
        )

    @app.get("/api/case-taxonomy")
    async def taxonomy():
        return await asyncio.to_thread(store.taxonomy_view)

    @app.put("/api/case-taxonomy")
    async def taxonomy_update(data: TaxonomyCommand):
        return await asyncio.to_thread(store.taxonomy_update, data.model_dump())

    @app.put("/api/cases/{id}/classification")
    async def classify_case(id: UUID, data: CaseClassification):
        return await asyncio.to_thread(store.classify_case, str(id), data.revision,
                                      data.case_revision, [m.model_dump() for m in data.memberships])

    @app.post("/api/cases/classify-models")
    async def classify_models(revision: int = Query(..., ge=0)):
        return await asyncio.to_thread(store.classify_models, revision)

    @app.get("/api/cases/{id}")
    async def get_case(id: UUID):
        return await asyncio.to_thread(store.classified_case, str(id))

    @app.get("/api/cases/{id}/examples")
    async def response_examples(id: UUID):
        return await asyncio.to_thread(store.response_examples, str(id))

    @app.post("/api/cases/{id}/examples")
    async def save_response_example(id: UUID, data: ResponseExampleSave):
        return await asyncio.to_thread(store.save_response_example, str(id), str(data.run_id))

    @app.get("/api/cases/{id}/examples/{example_id}")
    async def response_example(id: UUID, example_id: UUID):
        def read():
            item, record = store.response_example(str(id), str(example_id))
            path = store.path("runs", record["id"]) / "response.body.txt"
            if path.exists() and not record.get("is_sse"):
                with path.open("r", encoding="utf-8") as f:
                    record["response_preview"] = f.read(2 * 1024 * 1024)
                    record["preview_truncated"] = bool(f.read(1))
            return {"example": item, "run": record}
        return await asyncio.to_thread(read)

    async def save_case(data, id=None):
        try:
            parsed = RunInput.model_validate(
                {
                    **data.request,
                    "environment_id": str(uuid4()),
                    "submission_key": str(uuid4()),
                }
            ).model_dump(mode="json")
        except ValueError:
            raise ValueError("用例字段或资源绑定格式不正确") from None
        for field in ["environment_id", "submission_key", "case_id"]:
            parsed.pop(field, None)
        protector = Importer(store, vault, port)
        protector.discover(parsed)
        parsed["body"] = (
            protector.body(parsed["body"])[0]
            if parsed["body_type"] == "json"
            else protector.private_template(parsed["body"])
        )
        parsed["path"] = protector.url(parsed["path"])
        parsed["headers"] = {
            k: protector.secret(v) if sensitive(k) else protector.private_template(v)
            for k, v in parsed["headers"].items()
        }
        for binding in parsed["resource_bindings"]:
            resources.get(binding["resource_id"], binding["revision"])
        saved = await asyncio.to_thread(
            store.save_case, parsed, data.group, str(id) if id else None, data.revision
        )
        return await asyncio.to_thread(store.classified_case, saved["id"])

    @app.post("/api/cases")
    async def create_case(data: CaseSave):
        return await save_case(data)

    @app.put("/api/cases/{id}")
    async def update_case(id: UUID, data: CaseSave):
        return await save_case(data, id)

    @app.get("/api/resources")
    async def resource_list(
        page: int = Query(1, ge=1),
        limit: int = Query(30, ge=1, le=100),
        search: str = Query("", max_length=120),
        kind: str = Query("", pattern="^(|image|video|audio)$"),
    ):
        return await asyncio.to_thread(resources.list, page, limit, search, kind)

    @app.post("/api/resources/collect-history")
    async def collect_history(limit: int = Query(200, ge=1, le=1000)):
        if app.state.media_history_lock.locked():
            raise HTTPException(429, "已有历史资源采集正在执行")
        async with app.state.media_history_lock:
            return await asyncio.to_thread(resources.collect_history, limit)

    @app.post("/api/resources")
    async def create_resource(data: ResourceInput):
        return await asyncio.to_thread(resources.register, data)

    @app.put("/api/resources/{id}")
    async def update_resource(id: UUID, data: ResourceInput):
        return await asyncio.to_thread(resources.register, data, str(id))

    @app.get("/api/resources/{id}")
    async def resource_get(id: UUID, revision: int | None = Query(None, ge=1)):
        return await asyncio.to_thread(resources.get, str(id), revision)

    @app.post("/api/resources/{id}/archive")
    async def archive_resource(id: UUID, revision: int = Query(ge=1)):
        value = await asyncio.to_thread(resources.get, str(id))
        value["archived"] = True
        return await asyncio.to_thread(resources.save, value, revision)

    @app.post("/api/resources/{id}/file")
    async def upload_resource(
        id: UUID,
        request: Request,
        revision: int = Query(ge=1),
        filename: str = Query(min_length=1, max_length=180),
    ):
        return await resources.upload(str(id), revision, filename, request.stream())

    @app.api_route("/api/resources/{id}/file", methods=["GET", "HEAD"])
    async def resource_file(
        id: UUID, revision: int | None = Query(None, ge=1), download: bool = False
    ):
        value = await asyncio.to_thread(resources.get, str(id), revision)
        if not value["file"]:
            raise HTTPException(404, "资源还没有本地文件")
        file = value["file"]
        path = resources.object_path(file)
        return FileResponse(
            path,
            media_type=file["mime"],
            filename=file["filename"],
            content_disposition_type="attachment" if download else "inline",
        )

    @app.post("/api/resources/{id}/download")
    async def download_resource(id: UUID, data: ResourceDownload):
        return await resources.start_download(
            str(id), data.revision, str(data.address_id)
        )

    @app.get("/api/resource-jobs")
    async def resource_jobs():
        with store.lock:
            rows = store.db.execute(
                "SELECT summary FROM resource_jobs ORDER BY created DESC LIMIT 20"
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    @app.get("/api/resource-jobs/{id}")
    async def resource_job(id: UUID):
        return await asyncio.to_thread(resources.job, str(id))

    @app.post("/api/resource-jobs/{id}/cancel")
    async def cancel_resource_job(id: UUID):
        return await resources.cancel(str(id))

    @app.get("/api/domains")
    async def domains():
        return await asyncio.to_thread(resources.domains)

    @app.get("/api/runs")
    async def runs(
        page: int = Query(1, ge=1, le=100000),
        limit: int = Query(50, ge=1, le=200),
        environment_id: UUID | None = None,
    ):
        return await asyncio.to_thread(
            store.list_runs,
            page,
            limit,
            str(environment_id) if environment_id else None,
        )

    @app.get("/api/runs/{id}")
    async def run(id: UUID):
        record = await asyncio.to_thread(store.run, str(id))
        path = store.path("runs", id) / "response.body.txt"
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                record["response_preview"] = f.read(262144)
            record["preview_truncated"] = path.stat().st_size > 262144
        return record

    @app.get("/api/runs/{id}/events")
    async def events(id: UUID, offset: int = Query(0, ge=0), full: bool = False):
        await asyncio.to_thread(store.run, str(id))
        path = store.path("runs", id) / "stream.events.ndjson"
        if not path.exists():
            return {"items": [], "offset": 0, "has_more": False}
        if offset > path.stat().st_size:
            raise ValueError("事件偏移量不合法")
        items = []
        read = 0
        with path.open("rb") as f:
            f.seek(offset)
            while len(items) < 50 and read < 262144:
                before = f.tell()
                line = f.readline(4 * 1024 * 1024 + 1)
                if not line:
                    break
                if not line.endswith(b"\n"):
                    f.seek(before)
                    break
                read += len(line)
                if len(line) > 262144 and not full:
                    items.append(
                        {
                            "event": "preview_omitted",
                            "data": "单事件较大，界面预览略",
                            "elapsed_ms": None,
                        }
                    )
                else:
                    items.append(json.loads(line))
            offset = f.tell()
        return {"items": items, "offset": offset, "has_more": offset < path.stat().st_size}

    @app.post("/api/runs/{id}/cancel")
    async def cancel(id: UUID):
        return await engine.cancel(str(id))

    @app.post("/api/service/stop")
    async def stop():
        callback = getattr(app.state, "request_shutdown", None)
        if not callback:
            raise HTTPException(409, "当前运行方式不支持远程停止")
        callback()
        return {"ok": True}

    dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/")
        async def index():
            return FileResponse(dist / "index.html")

    return app
