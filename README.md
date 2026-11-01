# ReqDock

**轻量、按需运行的本地 API 与 SQL 工作台，让开发者和 AI Agent 共用用例与记录。**

A lightweight, on-demand local workspace for APIs, SQL, and AI agents.

在一个工作台里发送 API 请求、查看流式响应、管理测试资源、查询数据库。用例、环境和执行记录保存在本机，手动调试与 AI 自动测试可以接着同一份数据继续工作。

## 为什么选择 ReqDock

| 优点 | 对日常工作的帮助 |
| --- | --- |
| **轻量运行，按需启动** | Windows 客户端复用系统 WebView2；打开客户端才启动本地服务，关闭后服务与窗口子进程一起退出，减少后台常驻占用。 |
| **面向低内存占用设计** | 请求并发和等待队列有上限，响应体与流式记录有大小限制；流式展示按窗口分段加载，避免把全部历史事件持续留在页面内存中。后端还会检查内存占用，超出准入阈值时拒绝新增 API 请求。 |
| **数据库连接及时回收** | 每个连接配置下的每个库最多保留 3 条连接，空闲 60 秒自动回收；SQL 执行线程按需创建、完成后退出，查询最长 5 分钟。 |
| **一个入口处理 API 与 SQL** | API 调试、MySQL/MariaDB 查询和媒体资源管理放在同一个程序中，两个工作空间的数据分别保存。 |
| **手动操作与 AI 测试共用数据** | 界面、CLI 和 Skill 复用同一套环境、用例、资源与请求记录，减少重复配置；AI 执行过的请求可以回到界面查看。 |
| **本地保存，开箱无需云账号** | 不需要注册 ReqDock 云账号，也不需要部署 Docker、服务端数据库或云服务。数据保存在本机，密钥使用 Windows 系统凭证库保护。被测接口和数据库仍使用各自的账号与权限。 |
| **每次调试都有记录可查** | 自动保存请求快照、请求头、响应头、响应内容和耗时，并对已知敏感字段脱敏；实际结果可保存为用例示例，下次继续查看。 |
| **用例与资源可以反复使用** | 支持粘贴 cURL、导入 OpenAPI、切换环境，以及复用图片、视频和音频资源，减少每次从头组装请求的工作。 |

**关于内存占用：** 上述是已经实现的资源控制机制。实际占用取决于 WebView2、打开的页面和数据量；目前没有发布统一的内存基准测试，不承诺固定 MB 数值或相对其他工具的降幅。内存准入检查针对 API 后端进程，不是整个客户端的系统级内存硬上限。

## 适合哪些场景

- **接口联调**：保存常用请求，切换本地、测试和线上环境，核对请求与响应。
- **模型接口验证**：手动或通过 Skill 发起已授权测试，查看 SSE、响应头、错误和历史结果。
- **数据库排查**：按环境管理连接，浏览库表和字段，执行常用 SQL、查看 EXPLAIN 并导出结果。
- **多媒体接口测试**：统一保存图片、视频、音频链接与文件，在用例中直接复用。

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
