# ReqDock

A local workspace for APIs, SQL, and AI agents.

面向开发者与 AI Agent 的本地 API 和 SQL 工作台。请求用例、环境、媒体资源和执行记录保存在本机，界面与 CLI 共用同一套数据。

## Features / 功能

- **API workspace**: HTTP/JSON requests, cURL paste import, environment variables, reusable cases, response examples and request history.
- **Streaming**: SSE events, scrollable response viewer, timings and response headers.
- **Media library**: image, video and audio links/files, previews and response-resource collection.
- **SQL workspace**: MySQL/MariaDB connections grouped by environment, database/table browser, persistent schema cache, saved SQL, selected-statement execution and EXPLAIN.
- **SQL results**: messages, summary, result grid and CSV/JSON export of saved results.
- **Controlled writes**: optional guarded UPDATE/DELETE and CREATE TABLE with confirmation. Database account permissions still apply.
- **Agent interface**: CLI and a portable Skill share the application's cases, environments and execution records.
- **On-demand desktop client**: opening the Windows client starts the local service; closing it stops the service.

## Requirements

Windows, Python 3.12+, Microsoft Edge WebView2. Node.js 24 is needed to build the frontend.
Windows Credential Manager and DPAPI protect credentials and private workspace data. Other operating systems are not currently supported.

## Install and run

```powershell
git clone https://github.com/TCMING/reqdock.git
cd reqdock
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
npm ci --prefix web --ignore-scripts
npm run build --prefix web
powershell -NoProfile -File scripts/build-client.ps1
.\ReqDock.exe
```

`ReqDock.exe` is a small launcher built from source, not a standalone installer. Keep it beside the project and virtual environment. To create desktop and Start menu shortcuts, run `scripts/install-client.ps1`. No compiled launcher, local environment definitions or credentials are distributed in this repository.

The browser address is http://127.0.0.1:18765 while the client is running. A new workspace uses paired access by default; the desktop client pairs its own window automatically.

For browser-only usage, start with `scripts/start.ps1 -OpenBrowser`, and stop with `scripts/stop.ps1`.

## CLI and Skill

With the client running:

```powershell
.\.venv\Scripts\python.exe -m api_post.skill_cli status
.\.venv\Scripts\python.exe -m api_post.skill_cli environments
.\.venv\Scripts\python.exe -m api_post.skill_cli cases --search example
.\.venv\Scripts\python.exe -m api_post.skill_cli run --case CASE_UUID --environment ENVIRONMENT_NAME --platform PLATFORM_NAME --wait 30
```

See [the portable Skill](skills/reqdock/SKILL.md). Internal Python module names remain `api_post` for compatibility.

## Data and operational limits

All user data lives in the ignored `workspace/` directory. Never commit it, exported connection profiles, request histories, signed media URLs or real credentials. No existing local data is imported on startup. Credentials are stored in the current Windows user's credential manager; private stored data cannot simply be transferred to another user or machine.

The service binds to loopback and validates local origin/host. Do not expose it through a reverse proxy or public interface. Requests may reach explicitly configured private networks. Network scope checks and TLS verification remain enabled; outbound clients need independent security review before use in more demanding environments.

API requests: one active request and five queued; no automatic retry. Responses: 2 MiB ordinary body, 50 MiB SSE evidence. SQL pools: at most three connections per connection profile/database, idle connections reclaimed after 60 seconds, query deadline at most 300 seconds. SQL result exports contain only saved rows and do not rerun the query. Closing a local request does not guarantee a remote task stops or avoids billing.

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm run build --prefix web
npm run test:e2e --prefix web
```

Browser tests use installed Edge and isolated temporary workspaces. They must not use real provider credentials or production databases. SQL tests use local fixtures rather than requiring a database server.

This is an early Windows-first project. Native multipart upload, historical jobId reconstruction and cloud sync are not implemented. HTTP 200, a completed request and a passing assertion are different signals; none alone proves model quality.

## License

MIT. See [LICENSE](LICENSE).
