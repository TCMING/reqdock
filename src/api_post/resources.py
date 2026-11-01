"""Versioned media objects; one bounded transfer, pinned trusted downloads."""

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit, parse_qsl, unquote
from uuid import uuid4, uuid5, NAMESPACE_URL
import httpx
import psutil
from .network import origin, validate_environment, redact_url, PinnedTransport
from .private_data import transform
from .security import sensitive
from .storage import atomic, now

LIMITS = {"image": 50 * 1024**2, "audio": 512 * 1024**2, "video": 2 * 1024**3}
EXTENSIONS = {
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp"},
    "audio": {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".webm"},
    "video": {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".ogg"},
}


def media_type(header, kind):
    if kind == "image":
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if header.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if header.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
            return "image/webp"
    if kind == "audio":
        if header[:4] == b"RIFF" and header[8:12] == b"WAVE":
            return "audio/wav"
        if header.startswith(b"fLaC"):
            return "audio/flac"
        if header.startswith(b"ID3") or (
            len(header) > 1 and header[0] == 255 and header[1] & 224 == 224
        ):
            return "audio/mpeg"
    if kind in {"audio", "video"}:
        if header.startswith(b"OggS"):
            return kind + "/ogg"
        if header.startswith(b"\x1a\x45\xdf\xa3"):
            return kind + "/webm"
        if header[4:8] == b"ftyp":
            return kind + "/mp4"
    raise ValueError("文件签名与所选媒体类型不匹配；不接收 HTML、SVG 或可执行文件")


def inject(body, pointer, value):
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        raise ValueError("使用 /file_list/0/image_url 形式的 JSON 路径")
    parts = [p.replace("~1", "/").replace("~0", "~") for p in pointer[1:].split("/")]
    if len(parts) > 16 or any(
        p in {"__proto__", "prototype", "constructor"} for p in parts
    ):
        raise ValueError("资源字段路径不合法")
    current = body
    for i, key in enumerate(parts):
        last = i == len(parts) - 1
        if isinstance(current, list):
            if not re.fullmatch(r"0|[1-9][0-9]{0,2}", key) or int(key) > len(current):
                raise ValueError("数组下标越界，请先补齐请求结构")
            index = int(key)
            if index == len(current):
                current.append(None if last else ([] if parts[i + 1].isdigit() else {}))
            if last:
                current[index] = value
                return
            current = current[index]
        elif isinstance(current, dict):
            if last:
                current[key] = value
                return
            if key not in current:
                current[key] = [] if parts[i + 1].isdigit() else {}
            current = current[key]
        else:
            raise ValueError("资源路径经过了非对象字段")


class ResourceManager:
    def __init__(self, store, vault, port):
        self.store = store
        self.vault = vault
        self.port = port
        self.tasks = {}
        self.slot = asyncio.Lock()
        self.download_admission = asyncio.Lock()
        self.uploading = False
        self.root = store.root / "resources"
        self.root.mkdir(exist_ok=True)
        for name in ["objects", "tmp", "jobs"]:
            (self.root / name).mkdir(exist_ok=True)
        with store.lock:
            store.db.execute(
                "CREATE TABLE IF NOT EXISTS resources (id TEXT PRIMARY KEY, summary TEXT)"
            )
            store.db.execute(
                "CREATE TABLE IF NOT EXISTS media_objects (hash TEXT PRIMARY KEY, size INTEGER)"
            )
            store.db.execute(
                "CREATE TABLE IF NOT EXISTS resource_jobs (id TEXT PRIMARY KEY, resource_id TEXT, created TEXT, summary TEXT)"
            )
            store.db.commit()
            for p in self.root.glob("*/current.json"):
                manifest = json.loads(p.read_text(encoding="utf-8"))
                self.index(manifest)
                # Immutable older revisions can reference distinct content objects.
                for revision in (p.parent / "revisions").glob("*.json"):
                    file = json.loads(revision.read_text(encoding="utf-8")).get("file")
                    if file:
                        store.db.execute(
                            "INSERT OR IGNORE INTO media_objects VALUES (?,?)",
                            (file["sha256"], file["size"]),
                        )
                store.db.commit()
            for p in (self.root / "jobs").glob("*.json"):
                j = json.loads(p.read_text(encoding="utf-8"))
                if j["state"] in {"queued", "running"}:
                    j.update(
                        state="interrupted", error="客户端或服务已退出，下载未自动重试"
                    )
                    self.save_job(j)
            for p in (self.root / "tmp").glob("*.part"):
                if p.resolve().is_relative_to((self.root / "tmp").resolve()):
                    p.unlink()

    def index(self, value):
        with self.store.lock:
            self.store.db.execute(
                "INSERT INTO resources VALUES (?,?) ON CONFLICT(id) DO UPDATE SET summary=excluded.summary",
                (value["id"], json.dumps(value, ensure_ascii=False)),
            )
            self.store.db.commit()

    def get(self, id, revision=None):
        folder = self.store.path("resources", id)
        path = (
            folder / "revisions" / f"{revision}.json"
            if revision
            else folder / "current.json"
        )
        if not path.exists():
            raise KeyError("资源不存在")
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, value, expected=None):
        with self.store.lock:
            id = value["id"]
            folder = self.store.path("resources", id)
            old = self.get(id) if (folder / "current.json").exists() else None
            if old and expected != old["revision"]:
                raise ValueError("资源版本已变化，请刷新后再操作")
            value = {
                **value,
                "revision": old["revision"] + 1 if old else 1,
                "schema_version": 1,
                "created_at": old["created_at"] if old else now(),
                "updated_at": now(),
            }
            atomic(folder / "revisions" / f"{value['revision']}.json", value)
            atomic(folder / "current.json", value)
            self.index(value)
            return value

    def register(self, model, id=None, create_id=None):
        data = model.model_dump(mode="json")
        if len({a["purpose"] for a in data["addresses"]}) != len(data["addresses"]):
            raise ValueError("每个资源的内网、外网、第三方链接各最多保留一个")
        if any(len(tag) > 80 for tag in data["tags"]):
            raise ValueError("单个标签最多 80 个字符")
        old = self.get(id) if id else None
        if old and data["revision"] != old["revision"]:
            raise ValueError("资源版本已变化，请刷新后保存")
        if old and old["file"] and old["kind"] != data["kind"]:
            raise ValueError("已有文件的资源不能修改媒体类型")
        id = str(id or create_id or uuid4())
        folder = self.store.path("resources", id)
        folder.mkdir(parents=True, exist_ok=True)
        addresses = []
        for a in data["addresses"]:
            if a["url"] is None:
                previous = next(
                    (v for v in (old or {}).get("addresses", []) if v["id"] == a["id"]),
                    None,
                )
                if not previous:
                    raise ValueError("保留链接时必须指定当前资源中的链接 ID")
                if any(
                    a[k] != previous[k]
                    for k in ("purpose", "network_scope", "credential_ref")
                ):
                    raise ValueError("修改链接类型或凭证时请重新填写完整链接")
                addresses.append({**previous, "trusted": a["trusted"]})
                continue
            scheme, host, port = origin(a["url"])
            base = scheme + "://" + urlsplit(a["url"]).netloc
            env = {"base_url": base, "network_scope": a["network_scope"]}
            validate_environment(env, self.port)
            if a["credential_ref"] and not any(
                c["id"] == a["credential_ref"] for c in self.store.credential_meta()
            ):
                raise ValueError("链接凭证不存在")
            domain_id = str(
                uuid5(NAMESPACE_URL, "media-domain:" + base + ":" + a["network_scope"])
            )
            domain = {
                "id": domain_id,
                "origin": base,
                "host": host,
                "port": port,
                "network_scope": a["network_scope"],
            }
            atomic(self.store.path("domains", domain_id) / "current.json", domain)
            a_id = str(uuid4())
            relative = "links/" + a_id + ".dpapi"
            private = folder / relative
            private.parent.mkdir(exist_ok=True)
            private.write_bytes(transform(a["url"].encode()))
            addresses.append(
                {
                    **a,
                    "url": redact_url(a["url"]),
                    "id": a_id,
                    "domain_id": domain_id,
                    "private_ref": relative,
                    "verified_sha256": None,
                }
            )
        value = {
            **(old or {}),
            "id": id,
            "name": data["name"].strip(),
            "kind": data["kind"],
            "tags": data["tags"],
            "addresses": addresses,
            "file": old.get("file") if old else None,
            "archived": False,
        }
        if not value["name"]:
            raise ValueError("资源名称不能为空")
        return self.save(value, old["revision"] if old else None)

    def list(self, page=1, limit=30, search="", kind="", include_archived=False):
        with self.store.lock:
            where = []
            params = []
            if not include_archived:
                where.append("COALESCE(json_extract(summary,'$.archived'),0)=0")
            if search:
                where.append("(json_extract(summary,'$.name') LIKE ? ESCAPE '\\' OR json_extract(summary,'$.file.filename') LIKE ? ESCAPE '\\')")
                params.append(
                    "%"
                    + search.replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                    + "%"
                )
                params.append(params[-1])
            if kind:
                where.append("json_extract(summary,'$.kind')=?")
                params.append(kind)
            clause = " WHERE " + " AND ".join(where) if where else ""
            total = self.store.db.execute(
                "SELECT COUNT(*) FROM resources" + clause, params
            ).fetchone()[0]
            rows = self.store.db.execute(
                "SELECT summary FROM resources"
                + clause
                + " ORDER BY json_extract(summary,'$.updated_at') DESC LIMIT ? OFFSET ?",
                [*params, limit, (page - 1) * limit],
            ).fetchall()
            return {
                "items": [self.describe(json.loads(r[0])) for r in rows],
                "total": total,
                "page": page,
                "limit": limit,
            }

    def describe(self, value):
        # Only expose paths derived from validated content hashes within this workspace.
        local_path = None
        file_missing = False
        if value.get("file"):
            try:
                local_path = str(self.object_path(value["file"]).resolve())
            except ValueError:
                file_missing = True
        return {**value, "local_path": local_path, "file_missing": file_missing}

    def domains(self):
        return [
            json.loads(p.read_text(encoding="utf-8"))
            for p in (self.store.root / "domains").glob("*/current.json")
        ]

    def private_url(self, value, address):
        path = self.store.path("resources", value["id"]) / address["private_ref"]
        if not path.resolve().is_relative_to(
            self.store.path("resources", value["id"]).resolve()
        ):
            raise ValueError("资源路径不合法")
        return transform(path.read_bytes(), decrypt=True).decode()

    def object_path(self, file):
        hash = file["sha256"]
        if not re.fullmatch("[0-9a-f]{64}", hash):
            raise ValueError("文件哈希不合法")
        path = self.root / "objects" / hash[:2] / hash
        if not path.resolve().is_relative_to((self.root / "objects").resolve()):
            raise ValueError("文件路径不合法")
        if not path.is_file():
            raise ValueError("本地文件已丢失，请重新导入")
        return path

    def capacity(self, additional=0):
        if psutil.Process().memory_info().private > 350 * 1024**2:
            raise ValueError("内存超过传输准入阈值，请稍后重试")
        if shutil.disk_usage(self.root).free - additional < 1024**3:
            raise ValueError("磁盘可用空间不足 1 GiB")
        with self.store.lock:
            size = self.store.db.execute(
                "SELECT COALESCE(SUM(size),0) FROM media_objects"
            ).fetchone()[0]
            runs = self.store.db.execute(
                "SELECT COALESCE(SUM(COALESCE(json_extract(summary,'$.evidence_bytes'),0)+COALESCE(json_extract(summary,'$.snapshot_bytes'),0)),0) FROM runs"
            ).fetchone()[0]
        if size + additional > 20 * 1024**3 or size + runs + additional > 25 * 1024**3:
            raise ValueError("资源或工作区容量预算不足")

    def commit(self, value, path, size, sha, filename, address_id=None, upload=False):
        with self.store.lock:
            current = self.get(value["id"])
            if current["revision"] != value["revision"] or current["archived"]:
                raise ValueError("资源版本已变化，未绑定本次文件")
            if (
                upload
                and Path(filename).suffix.lower() not in EXTENSIONS[value["kind"]]
            ):
                raise ValueError("文件扩展名不在支持范围")
            with path.open("rb") as f:
                mime = media_type(f.read(64), value["kind"])
            filename = re.sub(r'[\x00-\x1f\x7f/\\:<>"|?*]', "_", filename)[:160]
            if not Path(filename).suffix:
                filename += {
                    "image/png": ".png",
                    "image/jpeg": ".jpg",
                    "image/gif": ".gif",
                    "image/webp": ".webp",
                    "video/mp4": ".mp4",
                    "audio/mp4": ".m4a",
                    "video/webm": ".webm",
                    "audio/webm": ".webm",
                    "audio/wav": ".wav",
                    "audio/mpeg": ".mp3",
                    "audio/flac": ".flac",
                    "audio/ogg": ".ogg",
                    "video/ogg": ".ogg",
                }.get(mime, "")
            destination = self.root / "objects" / sha[:2] / sha
            destination.parent.mkdir(exist_ok=True)
            if not destination.resolve().is_relative_to(
                (self.root / "objects").resolve()
            ):
                raise ValueError("文件路径不合法")
            if destination.exists():
                if destination.stat().st_size != size:
                    raise ValueError("现有内容对象损坏")
                path.unlink()
            else:
                os.replace(path, destination)
            self.store.db.execute(
                "INSERT OR IGNORE INTO media_objects VALUES (?,?)", (sha, size)
            )
            self.store.db.commit()
            value["file"] = {
                "sha256": sha,
                "size": size,
                "mime": mime,
                "filename": Path(filename).name[:180],
                "saved_at": now(),
                "source": "upload" if upload else "download",
            }
            for a in value["addresses"]:
                if a["id"] == address_id:
                    a["verified_sha256"] = sha
                    a["verified_at"] = now()
            return self.save(value, value["revision"])

    async def upload(self, id, revision, filename, chunks):
        if self.uploading or self.slot.locked():
            raise ValueError("已有文件传输任务，请稍后再上传")
        self.uploading = True
        try:
            return await self._upload(id, revision, filename, chunks)
        finally:
            self.uploading = False

    async def _upload(self, id, revision, filename, chunks):
        if self.slot.locked():
            raise ValueError("已有文件传输任务，请稍后再上传")
        value = await asyncio.to_thread(self.get, id)
        if value["revision"] != revision or value["archived"]:
            raise ValueError("资源版本已变化或已归档")
        if Path(filename).suffix.lower() not in EXTENSIONS[value["kind"]]:
            raise ValueError("文件扩展名不在支持范围")
        await asyncio.to_thread(self.capacity)
        path = self.root / "tmp" / (str(uuid4()) + ".part")
        size = 0
        digest = hashlib.sha256()
        async with self.slot:
            try:
                with path.open("wb") as f:
                    async for chunk in chunks:
                        size += len(chunk)
                        if size > LIMITS[value["kind"]]:
                            raise ValueError("文件超过此媒体类型的大小上限")
                        if size % (1024**2) < len(chunk):
                            await asyncio.to_thread(self.capacity, size)
                        digest.update(chunk)
                        await asyncio.to_thread(f.write, chunk)
                    await asyncio.to_thread(f.flush)
                if not size:
                    raise ValueError("文件为空")
                await asyncio.to_thread(self.capacity, size)
                return await asyncio.to_thread(
                    self.commit,
                    value,
                    path,
                    size,
                    digest.hexdigest(),
                    filename,
                    None,
                    True,
                )
            finally:
                path.unlink(missing_ok=True)

    def save_job(self, job):
        with self.store.lock:
            atomic(self.root / "jobs" / (job["id"] + ".json"), job)
            self.store.db.execute(
                "INSERT INTO resource_jobs VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET summary=excluded.summary",
                (
                    job["id"],
                    job["resource_id"],
                    job["created_at"],
                    json.dumps(job, ensure_ascii=False),
                ),
            )
            self.store.db.commit()

    def job(self, id):
        with self.store.lock:
            row = self.store.db.execute(
                "SELECT summary FROM resource_jobs WHERE id=?", (str(id),)
            ).fetchone()
            if not row:
                raise KeyError("下载记录不存在")
            return json.loads(row[0])

    async def start_download(self, id, revision, address_id):
        async with self.download_admission:
            return await self._start_download(id, revision, address_id)

    async def _start_download(self, id, revision, address_id):
        if len(self.tasks) >= 6:
            raise ValueError("下载队列已满")
        value = await asyncio.to_thread(self.get, id)
        if value["revision"] != revision or value["archived"]:
            raise ValueError("资源版本已变化或已归档")
        address = next((a for a in value["addresses"] if a["id"] == address_id), None)
        if not address or not address["trusted"]:
            raise ValueError("请先编辑资源并明确确认此下载来源")
        if any(self.job(j)["resource_id"] == id for j in self.tasks):
            raise ValueError("该资源已有下载任务")
        job = {
            "id": str(uuid4()),
            "resource_id": id,
            "resource_revision": revision,
            "state": "queued",
            "received_bytes": 0,
            "created_at": now(),
            "address_id": address_id,
        }
        await asyncio.to_thread(self.save_job, job)
        task = asyncio.create_task(self.download(job, value, address))
        self.tasks[job["id"]] = task
        task.add_done_callback(lambda t: self.tasks.pop(job["id"], None))
        return job

    async def download(self, job, value, address):
        path = self.root / "tmp" / (job["id"] + ".part")
        digest = hashlib.sha256()
        size = 0
        try:
            async with self.slot:
                job["state"] = "running"
                await asyncio.to_thread(self.save_job, job)
                url = await asyncio.to_thread(self.private_url, value, address)
                env = {
                    "base_url": origin(url)[0] + "://" + urlsplit(url).netloc,
                    "network_scope": address["network_scope"],
                }
                headers = {"Accept-Encoding": "identity"}
                if address.get("credential_ref"):
                    ref = address["credential_ref"]
                    meta = next(
                        (c for c in self.store.credential_meta() if c["id"] == ref),
                        None,
                    )
                    secret = await asyncio.to_thread(
                        self.vault.get, "credential:" + ref
                    )
                    if not meta or not secret:
                        raise ValueError("下载凭证缺失或已撤销")
                    headers[meta["header"]] = meta["prefix"] + secret
                async with asyncio.timeout(1800):
                    async with httpx.AsyncClient(
                        transport=PinnedTransport(env, self.port),
                        trust_env=False,
                        follow_redirects=False,
                        timeout=httpx.Timeout(60, connect=10),
                    ) as client:
                        async with client.stream(
                            "GET", url, headers=headers
                        ) as response:
                            if response.status_code != 200:
                                raise ValueError(
                                    "下载失败：HTTP "
                                    + str(response.status_code)
                                    + "；未跟随重定向"
                                )
                            if response.headers.get(
                                "content-encoding", "identity"
                            ) not in {"identity", ""}:
                                raise ValueError("下载不接受压缩响应")
                            content_type = (
                                response.headers.get("content-type", "")
                                .split(";")[0]
                                .strip()
                            )
                            if (
                                content_type
                                and not content_type.startswith(value["kind"] + "/")
                                and content_type
                                not in {
                                    "application/octet-stream",
                                    "binary/octet-stream",
                                }
                            ):
                                raise ValueError("下载响应不是所选媒体类型")
                            declared = int(response.headers.get("content-length", "0"))
                            if declared < 0 or declared > LIMITS[value["kind"]]:
                                raise ValueError("文件声明大小超限")
                            await asyncio.to_thread(self.capacity, declared)
                            with path.open("wb") as f:
                                async for chunk in response.aiter_bytes(
                                    chunk_size=65536
                                ):
                                    size += len(chunk)
                                    if size > LIMITS[value["kind"]]:
                                        raise ValueError("文件实际大小超限")
                                    digest.update(chunk)
                                    await asyncio.to_thread(f.write, chunk)
                                    if size % (1024**2) < len(chunk):
                                        await asyncio.to_thread(self.capacity, size)
                                        job["received_bytes"] = size
                                        await asyncio.to_thread(self.save_job, job)
                                await asyncio.to_thread(f.flush)
                if not size:
                    raise ValueError("下载文件为空")
                await asyncio.to_thread(self.capacity, size)
                await asyncio.to_thread(
                    self.commit,
                    value,
                    path,
                    size,
                    digest.hexdigest(),
                    value["name"],
                    address["id"],
                    False,
                )
                job.update(
                    state="completed", received_bytes=size, sha256=digest.hexdigest()
                )
        except asyncio.CancelledError:
            job.update(state="cancelled", error="下载已取消，未保存不完整文件")
        except TimeoutError:
            job.update(state="timed_out", error="下载超时，未自动重试")
        except Exception as exc:
            job.update(
                state="failed",
                error=str(exc)[:160]
                if isinstance(exc, ValueError)
                else "下载失败：" + type(exc).__name__,
            )
        finally:
            path.unlink(missing_ok=True)
            job["ended_at"] = now()
            job["received_bytes"] = size
            await asyncio.to_thread(self.save_job, job)

    async def cancel(self, id):
        job = self.job(id)
        task = self.tasks.get(id)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                job.update(state="cancelled", error="排队下载已取消")
                self.save_job(job)
        return self.job(id)

    async def close(self):
        for id in list(self.tasks):
            await self.cancel(id)

    def collect_history(self, limit=200):
        from .response_media import ResponseMedia
        result = {"scanned": 0, "updated": 0, "resource_ids": [], "failed": 0, "limited": False}
        budget = 64 * 1024**2
        page = 1
        batch = min(100, limit)
        while result["scanned"] < limit and budget > 0:
            records = self.store.list_runs(page=page, limit=batch)
            if not records["items"]:
                break
            for summary in records["items"]:
                if result["scanned"] >= limit or budget <= 0:
                    break
                result["scanned"] += 1
                if summary["state"] in {"queued", "running"} or not 200 <= (summary.get("status_code") or 0) < 300:
                    continue
                discovered = ResponseMedia()
                folder = self.store.path("runs", summary["id"])
                try:
                    for name in ("response.body.txt", "stream.events.ndjson"):
                        path = folder / name
                        if not path.exists():
                            continue
                        if not path.resolve().is_relative_to(folder.resolve()):
                            raise ValueError("历史证据路径不合法")
                        allowance = min(budget, 8*1024**2 if name.endswith('ndjson') else 2*1024**2)
                        if name.endswith('ndjson'):
                            with path.open('rb') as stream:
                                consumed = 0
                                while consumed < allowance:
                                    line = stream.readline(min(2*1024**2+1, allowance-consumed))
                                    if not line:
                                        break
                                    consumed += len(line)
                                    budget -= len(line)
                                    if not line.endswith(b'\n'):
                                        result["limited"] = True
                                        break
                                    event = json.loads(line)
                                    if event.get('event') != 'error':
                                        discovered.text(event.get('data', ''))
                                if stream.read(1):
                                    result["limited"] = True
                        elif path.stat().st_size <= allowance:
                            text = path.read_text(encoding='utf-8')
                            budget -= path.stat().st_size
                            discovered.text(text)
                        else:
                            result["limited"] = True
                    if discovered.items:
                        captured, failures = self.capture_response(summary["id"], discovered)
                        result["failed"] += failures
                        result["limited"] |= discovered.limited
                        if captured:
                            with self.store.lock:
                                record = self.store.run(summary["id"])
                                references = {r['id']: r for r in record.get('response_resources', [])}
                                references.update({r['id']: r for r in captured})
                                record['response_resources'] = list(references.values())
                                record['resource_history_collected_at'] = now()
                                self.store.update_run(record)
                            result["updated"] += 1
                            result["resource_ids"].extend(r['id'] for r in captured)
                except Exception:
                    result["failed"] += 1
            if result["scanned"] >= records["total"]:
                break
            page += 1
        result["resource_ids"] = sorted(set(result["resource_ids"]))
        result["limited"] |= budget <= 0 or result["scanned"] < records["total"]
        return result

    def capture_response(self, run_id, discovered):
        from .models import ResourceInput
        from .response_media import link_scope
        captured = []
        failures = 0
        for candidate in discovered.items.values():
            try:
                content = candidate["content"]
                kind = candidate["kind"]
                url = candidate["url"]
                if content:
                    mime = media_type(content[:64], kind)
                    digest = hashlib.sha256(content).hexdigest()
                else:
                    mime = None
                    digest = hashlib.sha256(url.encode()).hexdigest()
                id = str(uuid5(NAMESPACE_URL, "response-media:"+kind+":"+digest))
                with self.store.lock:
                    if content:
                        # Reuse identical local content, including manually imported files.
                        row = self.store.db.execute(
                            "SELECT summary FROM resources WHERE json_extract(summary,'$.file.sha256')=? LIMIT 1", (digest,)
                        ).fetchone()
                        if row:
                            id = json.loads(row[0])["id"]
                    try:
                        value = self.get(id)
                    except KeyError:
                        filename = unquote(urlsplit(url).path.rsplit('/',1)[-1])[:90] if url else ''
                        name = filename or "响应"+{'image':'图片','video':'视频','audio':'音频'}[kind]+" "+digest[:10]
                        model = ResourceInput(name=name, kind=kind, tags=["响应自动入库"], addresses=[{
                            "purpose": "third_party", "url": url, "network_scope": link_scope(url), "trusted": False,
                        }] if url else [])
                        value = self.register(model, create_id=id)
                    if content and not value.get("file"):
                        self.capacity(len(content))
                        temporary = self.root / "tmp" / (uuid4().hex+".part")
                        try:
                            temporary.write_bytes(content)
                            value = self.commit(value, temporary, len(content), digest, "response", upload=False)
                            value["file"]["source"] = "response"
                        finally:
                            temporary.unlink(missing_ok=True)
                    sources = value.get("source_runs", [])
                    if run_id not in sources:
                        value = self.save({**value, "source_runs": [*sources,run_id][-20:]}, value["revision"])
                    captured.append({"id": id, "name": value["name"], "kind": kind, "revision": value["revision"], "archived": value.get("archived",False)})
            except Exception:
                # Resource capture is auxiliary and cannot change the request outcome.
                failures += 1
        return captured, failures

    def resolve(self, body, bindings, env, known):
        snapshots = []
        for binding in bindings:
            value = self.get(binding["resource_id"], binding["revision"])
            mode = binding["mode"]
            file = value.get("file")
            address = None
            if mode == "base64":
                if not file:
                    raise ValueError("资源尚无本地文件")
                if file["size"] > 4 * 1024**2:
                    raise ValueError("Base64 源文件超过 4 MiB，请使用链接")
                source = self.object_path(file)
                if source.stat().st_size != file["size"]:
                    raise ValueError("本地文件大小与记录不一致")
                payload = (
                    "data:"
                    + file["mime"]
                    + ";base64,"
                    + base64.b64encode(source.read_bytes()).decode()
                )
                known.append(payload)
            else:
                purpose = (
                    ("external" if env["network_scope"] == "public" else "internal")
                    if mode == "auto"
                    else mode
                )
                if mode == "url":
                    if len(value["addresses"]) != 1:
                        raise ValueError("资源需保留一个明确的URL，请先编辑资源")
                    address = value["addresses"][0]
                else:
                    address = next(
                        (a for a in value["addresses"] if a["purpose"] == purpose), None
                    )
                if not address:
                    raise ValueError("资源没有所选网络范围的链接，不自动跨网替换")
                payload = self.private_url(value, address)
                if any(sensitive(k) for k, v in parse_qsl(urlsplit(payload).query)):
                    known.append(payload)
            inject(body, binding["pointer"], payload)
            snapshots.append(
                {
                    "resource_id": value["id"],
                    "revision": value["revision"],
                    "name": value["name"],
                    "kind": value["kind"],
                    "mode": mode,
                    "pointer": binding["pointer"],
                    "sha256": file["sha256"] if file else None,
                    "address_id": address["id"] if address else None,
                    "url": address["url"] if address else None,
                }
            )
        return body, snapshots
