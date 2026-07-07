# aiTrade Agent Instructions

This repository uses AI assistants as implementation agents under owner approval.

Representative title: 대표님.

Core operating rule: the owner gives the initial instruction and approves the final result. AI agents must not require the owner to copy and paste messages between ChatGPT, Codex, and GitHub.

## Repository workflow

1. Work on a dedicated branch. Do not commit directly to `main` unless the owner explicitly says so.
2. Prefer pull requests for reviewable changes.
3. Keep commits grouped by meaningful work units. Do not create tiny commits for every minor edit.
4. Before changing code, read the relevant current-status and design documents.
5. Keep documentation minimal. Do not create new documents unless the task explicitly requires it. Prefer updating existing core documents, except for StockBoard v2 work, which has its own separate v2 document.
6. Use the latest PR or issue comments as task context when the owner points Codex at a GitHub item. Do not require the owner to copy long instructions between tools.

## StockBoard baseline documents

Use these as the primary legacy StockBoard references when present:

- `docs/STOCKBOARD_CURRENT_STATUS_20260625.md`
- `docs/STOCKBOARD_NAMEPLATE_v1.4_20260625.md`
- `docs/OPENAPI_HELP_MASTER_UPDATED_20260625.md`

## StockBoard v2 active reference

StockBoard v2 is a separate real-time pipeline and must not be mixed into the legacy baseline documents unless the owner explicitly requests a merge.

Active v2 reference:

- `docs/STOCKBOARD_V2_REALTIME_PIPELINE_20260707.md`

Active v2 files:

- `stockboard_v2_live.cmd`
- `realtime_v2/build_universe.py`
- `realtime_v2/collector32.py`
- `realtime_v2/worker64.py`
- `realtime_v2/worker64_guarded.py`
- `realtime_v2/market_session.py`
- `docs/stockboard_v2.html`
- `config/stockboard_market_calendar.json`

Active v2 URL:

- `http://127.0.0.1:8765/`

Active v2 command set:

```powershell
cd C:\aiTrade
git pull
.\stockboard_v2_live.cmd stop
.\stockboard_v2_live.cmd start
.\stockboard_v2_live.cmd status
```

## No-copy automation protocol

This section exists because new ChatGPT/Codex windows repeatedly lost context and asked the owner to paste long instructions again. Do not do that.

### Rule 1: GitHub is the source of handoff context

When a new chat or Codex task starts, first read repository context instead of asking the owner to paste prior chat content.

Minimum read set for StockBoard v2 work:

1. `AGENTS.md`
2. `docs/STOCKBOARD_V2_REALTIME_PIPELINE_20260707.md`
3. `stockboard_v2_live.cmd`
4. `realtime_v2/worker64_guarded.py`
5. `realtime_v2/collector32.py`
6. `docs/stockboard_v2.html`
7. `config/stockboard_market_calendar.json`

For legacy StockBoard work, read the baseline documents listed above as well.

### Rule 2: The owner should not be a message courier

Agents must not ask the owner to copy-paste:

- previous ChatGPT answers
- Codex prompts
- long handoff documents
- commit lists
- command blocks already present in repository docs
- file contents that GitHub tools can read

Acceptable owner input is limited to live observations that only the owner can provide, for example HTS screenshots, runtime console output, or market behavior observed during live trading.

### Rule 3: If context is missing, create or update a repository document

If an agent discovers that important context exists only in chat, the agent must create or update a concise repository document and commit it. Do not leave the handoff only in the chat transcript.

For StockBoard v2, update `docs/STOCKBOARD_V2_REALTIME_PIPELINE_20260707.md` unless the owner explicitly asks for a new dated document.

### Rule 4: Start-of-task checklist for new agents

When asked to continue StockBoard v2 work, do this before changing files:

```text
1. Confirm branch target: hot-priority-integrated-20260630 unless owner says otherwise.
2. Read AGENTS.md.
3. Read docs/STOCKBOARD_V2_REALTIME_PIPELINE_20260707.md.
4. Read the active v2 files relevant to the request.
5. Identify whether the request affects:
   - live data collection
   - worker data acceptance/guard logic
   - market-session policy
   - UI display only
   - launcher/runtime only
6. State the risk class before changing code.
```

### Rule 5: Commit and report in repository terms

Final reports must include repository paths and commits, not chat-only descriptions. Include:

```text
Changed files
Deleted files
New files
Commit hashes
Validation not run and why
Owner live-check needed
```

### Rule 6: Do not reintroduce obsolete v1 sidecars

The following v1 temporary files were removed after StockBoard v2 absorbed their purpose. Do not recreate them unless the owner explicitly asks for legacy v1 support.

```text
stockboard_live_with_program_net.cmd
scripts/stockboard_program_net_snapshot.py
scripts/run_stockboard_program_net_snapshot.cmd
scripts/start_stockboard_program_net_sidecar_hidden.ps1
scripts/stop_stockboard_program_net_sidecar.ps1
scripts/stop_stockboard_program_net_sidecar.cmd
scripts/stockboard_speed_recorder.py
scripts/run_stockboard_speed_recorder.cmd
```

Use v2's built-in status, stream metrics, event log, and daily state instead.

### Rule 7: Live-market safety policy

During live market hours, avoid broad UI or refactor work unless it directly fixes real-time correctness. For 09:00~09:05 testing, do not add visual decoration. Only diagnose:

```text
price/current value correctness
trade value correctness
stream latency
queue growth
stale/drop/lag warnings
market phase
row_source transition from seed_universe to realtime
```

## Safety and scope rules

- Do not change live trading behavior unless the task explicitly requires it.
- Do not bypass trading safety guards.
- Do not remove existing diagnostics without replacing them with equivalent or better diagnostics.
- Do not introduce automatic cloud upload or scheduled execution without owner approval.
- Prefer small, reviewable scope, but commit only after a meaningful unit of work is complete.
- Do not push `main`.
- Do not push tags unless the owner explicitly requests it.
- Never commit `data/runtime/`, `data/execution_charts/`, credentials, tokens, account files, or local secret files.

## Validation expectations

Report what was actually verified. Do not claim verification that was not run.

A final report must include:

- Changed files
- Summary of changes
- Commands or checks run
- Results of those checks
- Items not verified and why
- Commit hash, when a commit was created
- Push status
- Suggested next step

Before final report, run these checks when relevant:

- `git status --short`
- `git diff --check`
- relevant Python `py_compile` checks

## Pull request expectations

Every PR should include:

- Purpose
- Changed files
- Validation checklist
- Risk notes
- Owner verification needed, especially for Kiwoom/OpenAPI/HTS/live-session behavior

## Codex task style

When an issue or PR comment asks Codex to work:

- Restate the goal briefly.
- Identify the expected branch.
- List the allowed files or expected target areas.
- List forbidden changes.
- Include validation commands and manual checks.
- Finish with the required final report format.

## Review guidelines

- Treat hidden live-trading behavior changes as high-risk.
- Treat missing validation claims as high-risk.
- Treat new scheduled execution, cloud upload, or auto-start behavior as high-risk unless explicitly requested.
- Treat broad rewrites outside the task scope as high-risk.
- For StockBoard UI/API work, verify that display changes do not move calculation responsibility into the frontend unless explicitly requested.
