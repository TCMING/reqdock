# Windows memory comparison

Measured on 2026-11-02. This is a controlled single-machine comparison of the native client, local service and WebView2 descendants, not a whole-client hard limit or a universal performance claim.

## Results

All values below are **MiB**, the median of five one-second samples per phase. USS is private resident physical memory; private commit is committed allocation. Summed RSS contains repeated shared pages and is not used as exclusive memory usage.

| 同一测试流程 | 优化前私有驻留 | 优化后私有驻留 | 优化前私有提交 | 优化后私有提交 |
| --- | ---: | ---: | ---: | ---: |
| 窗口可见、完成相同操作后 | 322.77 | 312.45 | 405.93 | 393.21 |
| 最小化空闲 80 秒 | 300.98 | 163.39 | 382.12 | 379.53 |
| 恢复窗口后的短时采样 | 340.42 | 257.32 | 422.13 | 446.68 |

At minimized idle, total private resident memory fell **45.7%** (300.98 to 163.39 MiB); the WebView2 portion fell from **161.98 to 26.10 MiB**. Private commit was nearly unchanged (382.12 to 379.53 MiB). Visible-window differences were small and should not be interpreted as a stable foreground saving. The restored sample had higher private commit (422.13 to 446.68 MiB), so this measurement does not establish a lower allocation peak. Memory can grow again as active pages, media and caches are used.

WebView2's Low target is best effort and can swap memory to disk. The main measured benefit is reduced idle physical residency, not a permanent reduction of every allocation. These workload-specific numbers differ from a simple snapshot of an already-open user window; do not compare them directly with an earlier 274 MiB snapshot taken under different conditions.

## Changes

- Event-driven `MemoryUsageTargetLevel.Low` after a window has been minimized/hidden for 10 seconds, or inactive for 30 seconds. Activation restores Normal immediately. One existing WinForms UI-loop timer is armed only while needed; it stops after applying the target. No recurring collector, new worker thread or new dependency.
- Scripts are **not suspended**. Backend API/SQL execution and Skill calls continue while the window is minimized. No forced process-working-set trim, GPU disabling or browser security flag is introduced. Unsupported runtime APIs retain default behavior.
- Closing an audio/video preview explicitly pauses the media, removes its source and resets the element, releasing decoder/network resources even when another reference retains the DOM node. Open previews keep their existing playback behavior.
- SQL status polling is single-flight and response-driven: 600 ms while visible, 2 seconds while hidden, with immediate refresh when visible if no read is in flight. Slow reads cannot build up overlapping requests. Actual SQL execution, deadlines, confirmation and pool rules are unchanged.
- Existing SQL connections already recycle after 60 idle seconds; no interval or per-database limit was changed. Final measurements verified zero remaining SQL connections after idle.

## Reproduction

Prerequisites: Windows, installed WebView2, the locked project environment, a built frontend, and both global ReqDock Skills installed with `scripts/install-skills.ps1`. The benchmark uses only disposable local workspaces and loopback API/MySQL fixtures. No user workspace, real provider or external coding model is used. It briefly opens a separate QA window; avoid other heavy workloads during measurement.

Baseline application commit: `ff6ee037c4b98e76526291892f42b5e7aa1083b2`. Use a separate checkout with that version's production frontend build, then compare against the optimized checkout:

```powershell
.\.venv\Scripts\python.exe scripts/benchmark-memory.py --source C:\path\to\baseline --label before --output workspace/qa/memory-before.json
.\.venv\Scripts\python.exe scripts/benchmark-memory.py --source C:\path\to\optimized --label after --output workspace/qa/memory-after.json
```

Both runs: same WebView2 viewport and fresh profile, 20 API history records containing 260,030-byte local responses; then the same history page, five samples, minimize, wait 80 seconds, sample and restore. While minimized, the installed Codex and ClaudeCode entrypoints each execute an API request and a read-only SQL query (four operations total). Agent calls add two more API responses of the same size. By the idle sample, the ordinary 60-second SQL idle deadline has elapsed. The restored history and response detail must remain readable. The fixture coordinator is excluded from the measured process tree.

The tool records phase samples, source checkout/commit, candidate source fingerprints and native Low/Normal state. It closes its own service and deletes its disposable credential-store entries afterward. Reports, logs and profile data stay under ignored `workspace/qa/`, never in the public repository.

## Validation

- 106 backend tests passed, including idle cancellation, late timer events, close cleanup and unsupported-runtime fallback.
- 12 relevant browser flows passed in isolated batches: slow SQL reads do not overlap; closing an audio preview clears its source; API/SQL, global Skill records, saved examples, SSE scrolling, resource management and drafts retain their behavior. One large-batch pairing hit the existing ten-attempts-per-minute limit; its workbench case passed alone in a fresh fixture. Authentication limits were preserved.
- Both global Skill entrypoints completed API and SQL while the native QA window was minimized; SQL pools returned to zero, the memory target returned to Normal, and response detail was available after restoration.
- Production frontend build and focused static checks passed. Existing user data is outside the benchmark and is not moved or rewritten by this optimization.

API references: [Microsoft WebView2 performance guidance](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/performance) and [MemoryUsageTargetLevel semantics](https://learn.microsoft.com/en-us/dotnet/api/microsoft.web.webview2.core.corewebview2.memoryusagetargetlevel).
