# Agent attribution and unified execution records

## Scope and compatibility

API and SQL tasks accepted through ReqDock may carry `execution_context`:

```json
{"source":"skill","agent_name":"Codex","task_id":"example-task-42"}
```

Source is ui / cli / skill / api; missing context on a new REST submission defaults to api. Agent is optional (80 characters); task ID is optional (128 characters). Labels are trimmed and control characters rejected. Existing records without context remain unknown. Labels are caller-declared and never grant identity or permissions. Known request credentials / SQL passwords are redacted if reused as labels; arbitrary sensitive text cannot be reliably recognized, so labels must contain no secrets.

Both UI forms mark source ui. CLI defaults to cli; Skills explicitly pass `--source skill`. Agent and task labels are fresh execution metadata, not case contents inherited on replay. Request metadata is saved in the run, never added to the provider request body or headers. SQL confirmation / cancellation keeps the original context.

Only created execution tasks are indexed. Authentication failures, validation/policy rejections before task creation, catalog reads, configuration edits and polling requests do not create execution rows. Completed indicates execution termination; it does not establish business success. API HTTP status is separately shown. No aggregate success-rate metric is introduced.

## Storage and lightweight behavior

`execution_records` is a derived table in the existing workspace `index.sqlite3`, keyed by `(kind,id)` so API and SQL cannot overwrite one another. Columns: kind, id, created_at, name, environment, state, duration_ms, status_code, source, agent_name, task_id, mode. Indexes cover creation time, task, Agent and source. Existing SQLite locking and a 2 MiB page cache remain in use.

API `Store.index` and SQL `persist` update the projection in their existing SQLite transaction. Original API manifests and encrypted SQL evidence remain authoritative. The projection contains no headers, URL, SQL, request/response body, result rows or error text. No duplicated evidence, external database, collector thread, background scan or new dependency is added.

On startup API manifest recovery also rebuilds API projection rows. SQL setup backfills missing rows directly from existing SQL summary metadata, without decrypting SQL evidence or opening a database connection. Previous running/pending SQL rows are marked interrupted; a CREATE TABLE that was running is marked unknown, following existing recovery semantics. No old operation is replayed. Deleting the derived table is recoverable on restart; user evidence is not rewritten by SQL backfill.

The new view queries only when opened, filtered, paginated or manually refreshed. It retains one page, default 20 / maximum 100 records; details are fetched only on demand and released on leaving the view. Detail text previews are capped at 128K characters. Other workspaces remain mounted to preserve their drafts. Original live-execution polling is unchanged.

## Local interfaces

- POST `/api/runs`: optional execution_context; existing API behavior is retained.
- POST `/api/sql/jobs`: optional execution_context; original policies, timeout (at most 300 seconds), pool limit (3 per profile/database), idle recycling (60 seconds) and write confirmation remain unchanged.
- GET `/api/executions`: page, limit, kind, source, agent_name, task_id, environment, state, search. Exact metadata filters; search escapes LIKE wildcards for literal name/ID matching. All values use bound SQLite parameters. Stable descending ordering by time, kind and ID. The response contains items / total / page / limit.
- Detail routes remain GET `/api/runs/{id}` and GET `/api/sql/jobs/{id}`. Reading a record never executes it again.

All routes stay behind existing workspace access control, Host/Origin checks and CSRF rules where applicable. There are no new anonymous exceptions or elevated permissions. The app remains a local single-user workspace, not a multi-tenant monitoring server.

CLI adds run source/Agent/task flags, `records` filters and `sql-run` from a bounded UTF-8 file. The SQL CLI returns running or awaiting_confirmation as-is and never automatically confirms or retries a write. A failed or timed-out submission must not be blindly resubmitted.

## Verification

Tests exercise mixed API/SQL task filtering and lifecycle updates, metadata isolation from payloads, legacy recovery without decrypting SQL, redaction, authenticated access, bounded pagination, literal search and SQL injection input. CLI tests verify fresh source labels, filtering and the write-confirmation boundary. Browser coverage exercises UI + Skill API/SQL records, read-only details, no background list polling and unchanged API/SQL drafts across workspace switches.

Statistics and task timelines remain future work. Runtime memory targets require separate benchmarking; no fixed whole-client memory footprint is claimed.

Verified on Windows on 2026-11-02: 100 backend tests passed; TypeScript/Vite production build passed; four existing API/SQL/Skill browser flows passed, plus the new mixed-source unified-history flow (including no list polling, details and draft preservation) passed after the accessibility fix. The final new flow was also checked with a fixture-only screenshot. Gitleaks snapshot scan had no unreviewed findings; the pre-existing cURL fixture remains an explicitly reviewed fake-credential finding.

## Global Skill installation and existing workspaces

`scripts/install-skills.ps1` installs the same portable Skill into the current user's Codex `.agents/skills/reqdock` and Claude Code `.claude/skills/reqdock`. It saves only a checkout path and client label in each installed config. The launcher is standard-library-only, calls the configured virtual-environment CLI with separate subprocess arguments and fixes its working directory, so it can be invoked from any coding repository. It does not change client model/authentication/tool-permission settings or delete the old API Lab Skill. Existing ReqDock integration files are backed up before replacement; linked Skill target directories are rejected.

Optional `%LOCALAPPDATA%/ReqDock/runtime.json` contains schema_version, project_root and workspace paths. The desktop, service CLI and Skill CLI share `workspace_for`, bounded to an 8 KiB configuration with absolute paths and matching checkout identity. Explicit CLI workspace arguments override the per-user selection. `REQDOCK_RUNTIME_CONFIG` can select an alternate local configuration for isolated tests. Reusing an existing workspace preserves its path-derived credential-store identity and does not copy evidence or passwords. Runtime configuration is private local state, not a repository artifact.

Follow-up verification on 2026-11-02: 104 backend tests passed; both global Skill manifests and the repository manifest passed the Skill Creator validator. From another working directory, the installed Codex and ClaudeCode launchers each completed a simulated API request and read-only SQL query. All four execution records shared the requested task ID, with distinct Agent labels and source=skill. No remote coding model was invoked during this adapter test. Default, non-overridden connections were separately checked against the real existing workspace, with original data and client configuration preserved. Private verification reports and fixture data are excluded from Git.
