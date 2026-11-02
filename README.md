# ReqDock

**轻量的本地 API 与 SQL 工作台，让开发者与 AI Agent 共用工具、复用用例，并追踪执行记录。**

A lightweight, on-demand local workspace for APIs, SQL, and AI agents.

在一个工作台里管理 API 请求、SQL 查询和测试资源，减少重复配置与工具切换。现已支持 API / SQL 统一执行记录、调用来源与 Agent / 任务标识。界面与 CLI / Skill 共用本地 API 用例和请求记录，Agent 也可通过 SQL CLI 创建受现有策略保护的执行任务；成功率等汇总统计仍在规划中。

## 为什么选择 ReqDock

| 优点 | 对日常工作的帮助 |
| --- | --- |
| **轻量运行，按需启动** | Windows 客户端复用系统 WebView2；打开客户端才启动本地服务，关闭后服务与窗口子进程一起退出，减少后台常驻占用。 |
| **减少空闲内存占用** | WebView2 在最小化或后台空闲后请求低内存目标，激活时恢复；脚本和后台任务继续运行。关闭音视频预览时释放媒体源，慢 SQL 状态读取不再重叠。请求、队列、流式展示仍有原有上限。 |
| **数据库连接及时回收** | 每个连接配置下的每个库最多保留 3 条连接，空闲 60 秒自动回收；SQL 执行线程按需创建、完成后退出，查询最长 5 分钟。 |
| **一个入口处理 API 与 SQL** | API 调试、MySQL/MariaDB 查询和媒体资源管理放在同一个程序中，两个工作空间的数据分别保存。 |
| **手动操作与 Agent 调用共用 API 记录** | 界面、CLI 和 Skill 复用同一套 API 环境、用例、资源与请求记录；Agent 经 CLI / Skill 发起的 API 请求也会保存，可回到界面检查。界面操作标记为 UI，CLI / Skill 可声明来源、Agent 名称和任务标识，记录可按同一任务查询。 |
| **本地保存，开箱无需云账号** | 不需要注册 ReqDock 云账号，也不需要部署 Docker、服务端数据库或云服务。数据保存在本机，密钥使用 Windows 系统凭证库保护。被测接口和数据库仍使用各自的账号与权限。 |
| **API 与 SQL 执行有据可查** | API 自动保存请求快照、请求头、响应头、响应内容和耗时，并对已知敏感字段脱敏；SQL 独立保存语句、目标库、执行状态、耗时与结果。两类历史既可分别查看，也可在统一执行记录中筛选与查看详情；汇总统计仍在规划中。 |
| **用例与资源可以反复使用** | 支持粘贴 cURL、导入 OpenAPI、切换环境，以及复用图片、视频和音频资源，减少每次从头组装请求的工作。 |

**Windows 实测：** 在同机、同页面、同一模拟负载的对照流程中，最小化空闲 80 秒后的私有驻留从约 **301 MiB 降至 163 MiB（45.7%）**。私有提交基本持平，恢复窗口后的提交量可能上升；收益集中在空闲物理驻留，实际占用取决于页面、媒体与数据量。查看[测量条件、完整前后对比及复现方法](docs/memory-performance.md)。原 API 后端内存准入检查仍只针对后端进程，不是整个客户端的系统级硬上限。

## 适合哪些场景

- **接口联调**：保存常用请求，切换本地、测试和线上环境，核对请求与响应。
- **模型接口验证**：手动或通过 Skill 发起已授权测试，查看 SSE、响应头、错误和历史结果。
- **数据库排查**：按环境管理连接，浏览库表和字段，执行常用 SQL、查看 EXPLAIN 并导出结果。
- **多媒体接口测试**：统一保存图片、视频、音频链接与文件，在用例中直接复用。

## 已实现 / Available now

| 能力 | 当前可用范围 |
| --- | --- |
| **API 工作空间** | HTTP/JSON 请求、粘贴解析 cURL、导入 OpenAPI、环境变量、用例分类与结果示例。 |
| **API 请求记录** | 界面、CLI、Skill 经 ReqDock 发送的请求共享记录；可查看请求与响应、请求头与响应头、状态、耗时、错误及 SSE 事件。 |
| **Agent API / SQL 调用入口** | 通过 CLI / Skill 发起 API 请求，或使用 SQL CLI 创建查询 / EXPLAIN 任务；可声明来源、Agent 和任务标识。SQL 写入继续受原有开关和逐次确认限制，CLI 不自动确认。 |
| **统一执行记录** | 同一页面查看 API / SQL 元数据，按类型、来源、Agent、任务、环境和状态筛选，分页查看只读详情；原请求记录、SQL 历史和编辑草稿保留。 |
| **SQL 工作空间** | MySQL / MariaDB、按环境管理连接、库表浏览、字段与结构缓存、常用 SQL、选中语句执行和 EXPLAIN。 |
| **SQL 执行记录与导出** | 记录目标连接、环境、数据库、SQL、状态、耗时及已保存结果；查看消息、摘要和数据，导出 CSV / JSON。 |
| **受控 SQL 写入** | 可单独启用受保护的 UPDATE / DELETE 与 CREATE TABLE，并在执行前确认；数据库账号权限仍然生效。 |
| **媒体资源库** | 图片、视频、音频的 URL / 文件管理、预览、用例复用，以及从响应中采集资源。 |
| **轻量运行与资源回收** | WebView2 客户端按需启动与退出；限制请求并发、队列和响应大小，SQL 空闲连接 60 秒回收。 |

**当前记录能力的边界：** 来源与 Agent / 任务标识由调用方声明，仅供关联查询，不代表经过认证的身份，也不参与授权。旧记录标为来源未知，不猜测原始调用方。只记录已被接收并创建执行任务的 API / SQL 操作；在参数校验或权限检查阶段就被拒绝的调用，以及配置管理、目录查询等控制接口，暂不计入执行记录。HTTP 200 / 已完成不等于业务成功。

## 规划中 / Planned

以下尚未实现，不作为当前版本功能承诺，也不包含确定的交付日期。

- **使用情况统计**：按来源、Agent、任务和环境汇总调用次数、耗时与错误分布；区分执行状态、HTTP 状态和业务判定，避免把“已完成”直接当作业务成功。
- **任务时间线**：在现有任务标识筛选的基础上，提供跨 API / SQL 的执行顺序与关联视图。

**监控范围：只记录经过 ReqDock 发起的操作。** 不采集绕过 ReqDock 的 Agent 行为，不监听其他软件流量。当前统一列表复用原执行链路，只维护小型 SQLite 元数据索引，不复制请求 / SQL 正文与结果，不新增采集线程或常驻轮询；每页默认 20 条、最多 100 条，详情点击时才读取。界面切换保留 API 和 SQL 草稿；离开统一页面释放已打开的详情。首批聚焦 API 与 SQL。

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

## Codex / Claude Code 全局 Skill

仓库自带 `skills/reqdock/`，包含通用 `SKILL.md`、跨项目调用脚本与 Codex 展示元数据。安装后两个客户端共用一个 ReqDock 实例与工作区，无需为每个编码项目重复配置。

在 ReqDock 项目目录完成运行环境安装后执行：

```powershell
powershell -NoProfile -File scripts/install-skills.ps1
# 若要保留已有 ReqDock / API Lab 工作区，可显式指定原目录；不移动文件：
powershell -NoProfile -File scripts/install-skills.ps1 -Workspace 'C:\path\to\existing\workspace'
```

| 客户端 | 全局安装位置 | 使用方式 |
| --- | --- | --- |
| Codex | `~/.agents/skills/reqdock/` | 在新会话中使用 `$reqdock`，也可按任务自动选择。 |
| Claude Code | `~/.claude/skills/reqdock/` | 在新会话中使用 `/reqdock`。 |

可通过 `-Client Codex` 或 `-Client ClaudeCode` 单独安装。安装器不修改客户端的模型、登录、权限或其他 Skill 配置；已有同名 Skill 和 ReqDock 路径配置会先备份到 `%LOCALAPPDATA%/ReqDock/skill-backups/`。旧 `api-lab` Skill 保留，新版入口使用 `reqdock` 名称。

每个已安装 Skill 的 `config.json` 只保存程序路径与默认 Agent 名称，不包含密钥。`%LOCALAPPDATA%/ReqDock/runtime.json` 选择共享工作区，客户端、服务和 CLI 使用同一目录；沿用原路径时，原 Windows 凭证库归属不变。配置仅影响匹配的 ReqDock checkout，其他 checkout 不会自动采用它。

Skill 的 `scripts/reqdock.py` 可从任意编码项目执行，自动补充 `source=skill` 和 `Codex` / `ClaudeCode` 名称；执行时未给任务标识则生成新的 ID。需要关联多次 API / SQL 操作时应显式传入相同 `--task-id`。调用脚本不执行 Shell、不绕过本机权限，也不自动确认 SQL 写入。客户端关闭后可由 Skill 的 `start` 命令按需打开，不安装常驻采集服务。

**验证范围**：双端安装入口可用隔离的模拟 API / MySQL 服务实测，并确认统一记录中的 Agent 与任务标识；这不等同于已经验证各客户端远端模型会自动选择 Skill。加载新 Skill 可能需要新开会话，执行仍受各客户端原有权限设置约束。

## CLI and Skill

With the client running:

```powershell
.\.venv\Scripts\python.exe -m api_post.skill_cli status
.\.venv\Scripts\python.exe -m api_post.skill_cli environments
.\.venv\Scripts\python.exe -m api_post.skill_cli cases --search example
.\.venv\Scripts\python.exe -m api_post.skill_cli run --case CASE_UUID --environment ENVIRONMENT_NAME --platform PLATFORM_NAME --wait 30
```

为 Agent 调用标记来源并关联同一任务（名称仅为示例，不包含真实凭证）：

```powershell
.\.venv\Scripts\python.exe -m api_post.skill_cli run --case CASE_UUID --environment ENVIRONMENT_NAME --platform PLATFORM_NAME --source skill --agent Codex --task-id TASK_ID --wait 30
.\.venv\Scripts\python.exe -m api_post.skill_cli sql-run --connection CONNECTION_UUID --database DATABASE_NAME --sql-file query.sql --source skill --agent Codex --task-id TASK_ID --wait 30
.\.venv\Scripts\python.exe -m api_post.skill_cli records --task-id TASK_ID
```

`--source` 默认 `cli`，Skill 应显式使用 `--source skill`。SQL CLI 只创建执行任务，不自动确认写入，也不自动重试。等待结束后仍在运行时，保留运行 ID，读取 `/api/runs/{id}` 或 `/api/sql/jobs/{id}`；不要重新提交。统一列表位于工作空间下拉菜单的“统一执行记录”。

See [the portable Skill](skills/reqdock/SKILL.md) and [execution records design](docs/execution-records.md). Internal Python module names remain `api_post` for compatibility.

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
