# aiTrade Agent Instructions

This repository uses AI assistants as implementation agents under owner approval.

Representative title: 대표님.

Core operating rule: the owner gives the initial instruction and approves the final result. AI agents must not require the owner to copy and paste messages between ChatGPT, Codex, and GitHub.

## Repository workflow

1. Work on a dedicated branch. Do not commit directly to `main` unless the owner explicitly says so.
2. Prefer pull requests for reviewable changes.
3. Keep commits grouped by meaningful work units. Do not create tiny commits for every minor edit.
4. Before changing code, read the relevant current-status and design documents.
5. Keep documentation minimal. Do not create new documents unless the task explicitly requires it. Prefer updating existing core documents.
6. Use the latest PR or issue comments as task context when the owner points Codex at a GitHub item. Do not require the owner to copy long instructions between tools.

## StockBoard baseline documents

Use these as the primary StockBoard references when present:

- `docs/STOCKBOARD_CURRENT_STATUS_20260625.md`
- `docs/STOCKBOARD_NAMEPLATE_v1.4_20260625.md`
- `docs/OPENAPI_HELP_MASTER_UPDATED_20260625.md`

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
