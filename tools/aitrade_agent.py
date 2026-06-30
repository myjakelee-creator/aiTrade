#!/usr/bin/env python3
"""Minimal local runner for Codex CLI with repository policy checks."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


RESULT_RELATIVE_PATH = Path("data/runtime/aitrade_agent_result.json")
DEFAULT_TASK_RELATIVE_PATH = Path("data/runtime/aitrade_agent_task.txt")
ALLOWED_FILE_PREFIX = "# allowed-file:"
ALLOWED_GLOB_PREFIX = "# allowed-glob:"
DEFAULT_ISSUE_REPO = "myjakelee-creator/aiTrade"
ISSUE_TASK_MARKERS = ("[AITRADE_AGENT_TASK]", "[AITRADE_TASK_SPEC_CURRENT]")
STOCKBOARD_BASELINE_DOCS = (
    "docs/STOCKBOARD_CURRENT_STATUS_20260625.md",
    "docs/STOCKBOARD_NAMEPLATE_v1.4_20260625.md",
    "docs/OPENAPI_HELP_MASTER_UPDATED_20260625.md",
)
GOAL_FORBIDDEN_RULES = (
    "commit 금지",
    "push 금지",
    "서버 실행/재시작 금지",
    "HTS/OpenAPI 조작 금지",
    ".env 수정 금지",
    "CSV 수정 금지",
    "AHK 수정 금지",
    "launcher 수정 금지",
    "새 문서 생성 금지",
)
GOAL_PROFILES: dict[str, dict[str, object]] = {
    "readonly": {
        "description": "파일 수정 금지. allowed-file 없음. 진단/조사/계획 전용.",
        "allowed_files": (),
        "allowed_globs": (),
        "extra_rules": ("파일 수정 금지", "진단/조사/계획 전용으로만 작업"),
    },
    "agent": {
        "description": "Local Agent 스크립트와 도구 파일만 수정 가능.",
        "allowed_files": (
            "tools/aitrade_agent.py",
            "scripts/start_aitrade_agent.cmd",
            "scripts/run_aitrade_agent_once.cmd",
            "scripts/run_aitrade_agent_issue.cmd",
            "scripts/run_aitrade_agent_goal.cmd",
            "scripts/show_aitrade_agent_result.cmd",
        ),
        "allowed_globs": (),
        "extra_rules": (),
    },
    "price-lane": {
        "description": "StockBoard price lane backend 파일만 수정 가능.",
        "allowed_files": (
            "kiwoom_data_provider.py",
            "stockboard_store.py",
            "stockboard_server.py",
        ),
        "allowed_globs": (),
        "extra_rules": (),
    },
    "stockboard-ui": {
        "description": "StockBoard UI 문서/asset 파일만 수정 가능.",
        "allowed_files": ("docs/stockboard_v0_3_0_sample.html", "docs/assets/stockboard.css"),
        "allowed_globs": ("docs/assets/*.js",),
        "extra_rules": (),
    },
}


def run_command(
    args: list[str],
    cwd: Path | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def find_repo_root() -> Path:
    result = run_command(["git", "rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "git rev-parse failed").strip())
    return Path(result.stdout.strip()).resolve()


def git_status(repo_root: Path) -> list[str]:
    result = run_command(["git", "status", "--short", "--untracked-files=all"], cwd=repo_root)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "git status failed").strip())
    return result.stdout.splitlines()


def git_branch(repo_root: Path) -> str:
    result = run_command(["git", "branch", "--show-current"], cwd=repo_root)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    fallback = run_command(["git", "rev-parse", "--short", "HEAD"], cwd=repo_root)
    if fallback.returncode == 0:
        return f"HEAD:{fallback.stdout.strip()}"
    return "unknown"


def normalize_repo_path(path_text: str, repo_root: Path) -> str:
    path_text = path_text.strip().replace("\\", "/")
    candidate = Path(path_text)
    if candidate.is_absolute():
        try:
            path_text = candidate.resolve().relative_to(repo_root).as_posix()
        except ValueError:
            path_text = candidate.as_posix()
    return path_text.strip("/")


def status_changed_files(status_lines: Iterable[str]) -> list[str]:
    changed: list[str] = []
    seen: set[str] = set()
    for line in status_lines:
        if not line.strip():
            continue
        path_text = line[3:] if len(line) > 3 else line.strip()
        if " -> " in path_text:
            paths = [part.strip() for part in path_text.split(" -> ", 1)]
        else:
            paths = [path_text.strip()]
        for path in paths:
            normalized = path.replace("\\", "/").strip().strip('"')
            if normalized and normalized not in seen:
                changed.append(normalized)
                seen.add(normalized)
    return changed


def status_lines_added_after(pre_status: list[str], post_status: list[str]) -> list[str]:
    pre_counts: dict[str, int] = {}
    for line in pre_status:
        pre_counts[line] = pre_counts.get(line, 0) + 1
    added: list[str] = []
    for line in post_status:
        count = pre_counts.get(line, 0)
        if count:
            pre_counts[line] = count - 1
        else:
            added.append(line)
    return added


def is_allowed(path: str, allowed_files: set[str], allowed_globs: list[str]) -> bool:
    if path in allowed_files:
        return True
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in allowed_globs)


def build_result(
    *,
    repo_root: Path,
    branch: str,
    pre_status: list[str],
    post_status: list[str],
    changed_files: list[str],
    violations: list[str],
    codex_exit_code: int | None,
    safe: bool,
    summary: str,
    issue_context: dict[str, object] | None = None,
    task_context: dict[str, object] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "timestamp": utc_now_iso(),
        "branch": branch,
        "repo_root": str(repo_root),
        "pre_status": pre_status,
        "post_status": post_status,
        "changed_files": changed_files,
        "violations": violations,
        "codex_exit_code": codex_exit_code,
        "safe": safe,
        "summary": summary,
    }
    if issue_context:
        result.update(issue_context)
    if task_context:
        result.update(task_context)
    return result


def write_result(repo_root: Path, result: dict[str, object]) -> Path:
    result_path = repo_root / RESULT_RELATIVE_PATH
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        errors="replace",
    )
    return result_path


def print_summary(result: dict[str, object], result_path: Path) -> None:
    print("aiTrade Local Agent result")
    print(f"summary: {result['summary']}")
    print(f"safe: {result['safe']}")
    print(f"branch: {result['branch']}")
    print(f"changed_files: {result['changed_files']}")
    print(f"violations: {result['violations']}")
    print(f"codex_exit_code: {result['codex_exit_code']}")
    for key in (
        "goal",
        "goal_profile",
        "task_source",
        "issue_number",
        "issue_repo",
        "issue_title",
        "issue_url",
        "task_marker",
        "task_fetched_at",
        "report_issue_number",
        "report_issue_repo",
        "report_issue_url",
        "report_dry_run",
        "report_comment_url",
    ):
        if key in result and result.get(key) is not None:
            print(f"{key}: {result.get(key)}")
    print(f"result_json: {result_path}")


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def read_prompt(args: argparse.Namespace, repo_root: Path) -> str:
    prompt_parts: list[str] = []
    if args.prompt:
        prompt_parts.append(args.prompt)
    if args.prompt_file:
        prompt_path = Path(args.prompt_file)
        if not prompt_path.is_absolute():
            prompt_path = repo_root / prompt_path
        prompt_parts.append(read_text_file(prompt_path))
    if not prompt_parts:
        default_prompt_path = repo_root / DEFAULT_TASK_RELATIVE_PATH
        if not default_prompt_path.exists():
            raise ValueError(
                "--prompt or --prompt-file was not provided, and the default task file "
                f"does not exist: {DEFAULT_TASK_RELATIVE_PATH.as_posix()}"
            )
        prompt_parts.append(read_text_file(default_prompt_path))
    prompt = "\n\n".join(part.strip() for part in prompt_parts if part and part.strip())
    if not prompt:
        raise ValueError("Prompt is empty. Add instructions to --prompt, --prompt-file, or the default task file.")
    return prompt


def goal_profile_config(profile: str) -> dict[str, object]:
    normalized = profile.strip().lower()
    if normalized not in GOAL_PROFILES:
        raise ValueError(
            f"Unknown --goal-profile: {profile}. Supported profiles: "
            + ", ".join(sorted(GOAL_PROFILES))
        )
    return GOAL_PROFILES[normalized]


def build_goal_task(goal: str, profile: str, repo_root: Path) -> tuple[str, dict[str, object]]:
    goal_text = goal.strip()
    if not goal_text:
        raise ValueError("--goal is empty")
    profile_name = profile.strip().lower()
    profile_config = goal_profile_config(profile_name)
    allowed_files = tuple(str(path) for path in profile_config["allowed_files"])
    allowed_globs = tuple(str(pattern) for pattern in profile_config["allowed_globs"])
    extra_rules = tuple(str(rule) for rule in profile_config["extra_rules"])

    lines: list[str] = []
    for path in allowed_files:
        lines.append(f"{ALLOWED_FILE_PREFIX} {normalize_repo_path(path, repo_root)}")
    for pattern in allowed_globs:
        lines.append(f"{ALLOWED_GLOB_PREFIX} {normalize_repo_path(pattern, repo_root)}")
    if lines:
        lines.append("")
    lines.extend(
        [
            f"목표: {goal_text}",
            f"profile: {profile_name}",
            "",
            "작업 전 필수 확인:",
            "- AGENTS.md를 먼저 읽고 지침을 따른다.",
            "- 관련 StockBoard 기준 문서를 UTF-8로 읽는다:",
        ]
    )
    for doc_path in STOCKBOARD_BASELINE_DOCS:
        lines.append(f"  - {doc_path}")
    lines.extend(
        [
            "- 코드 변경 전 `git status --short`를 먼저 확인한다.",
            "",
            "허용 파일 범위:",
        ]
    )
    if allowed_files:
        for path in allowed_files:
            lines.append(f"- {path}")
    if allowed_globs:
        for pattern in allowed_globs:
            lines.append(f"- {pattern}")
    if not allowed_files and not allowed_globs:
        lines.append("- 없음. 이 profile에서는 파일 수정 금지.")
    lines.extend(["", "금지 파일/작업:"])
    for rule in GOAL_FORBIDDEN_RULES:
        lines.append(f"- {rule}")
    for rule in extra_rules:
        lines.append(f"- {rule}")
    lines.extend(
        [
            "",
            "검증 명령:",
            "- `git diff --check` (변경이 있을 때)",
            "- 관련 Python 파일 변경 시 `python -m py_compile <file>`",
            "- 작업 성격에 맞는 최소 검증 명령",
            "- 마지막에 `git status --short`",
            "",
            "최종 보고 형식:",
            "- 변경 파일",
            "- 요약",
            "- 검증 명령과 결과",
            "- 미검증 항목과 이유",
            "- commit 없음",
            "- push 없음",
            "- suggested next step",
            "",
            "중요:",
            "- commit/push 금지.",
            "- 허용 파일 범위를 준수한다.",
            "- 금지 파일/작업을 준수한다.",
            "- 자동 cloud upload, scheduled execution, server start/restart를 하지 않는다.",
        ]
    )
    task = "\n".join(lines).strip() + "\n"
    context = {
        "task_source": "generated_goal",
        "goal": goal_text,
        "goal_profile": profile_name,
        "generated_task_preview": task,
        "goal_allowed_files": list(allowed_files),
        "goal_allowed_globs": list(allowed_globs),
    }
    return task, context


def parse_repo_from_remote(remote_url: str) -> str | None:
    remote_url = remote_url.strip()
    if not remote_url:
        return None
    scp_like = re.match(r"^(?:[^@]+@)?github\.com[:/](?P<repo>[^/]+/[^/]+?)(?:\.git)?/?$", remote_url)
    if scp_like:
        return scp_like.group("repo")
    parsed = urlparse(remote_url)
    if parsed.netloc.lower() == "github.com":
        path = parsed.path.strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        parts = path.split("/")
        if len(parts) >= 2 and parts[0] and parts[1]:
            return f"{parts[0]}/{parts[1]}"
    return None


def infer_issue_repo(repo_root: Path) -> str:
    result = run_command(["git", "remote", "get-url", "origin"], cwd=repo_root)
    if result.returncode == 0:
        repo = parse_repo_from_remote(result.stdout)
        if repo:
            return repo
    return DEFAULT_ISSUE_REPO


def github_api_via_gh(path: str, repo_root: Path) -> object:
    if shutil.which("gh") is None:
        raise RuntimeError("gh CLI is not available")
    result = run_command(["gh", "api", path], cwd=repo_root)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "gh api failed").strip())
    return json.loads(result.stdout)


def github_api_via_rest(path: str) -> object:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    url = f"https://api.github.com{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "aiTrade-local-agent",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub REST API failed ({exc.code}): {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"GitHub REST API failed: {exc.reason}") from exc
    return json.loads(body)


def github_post_via_gh(path: str, payload: dict[str, object], repo_root: Path) -> object:
    if shutil.which("gh") is None:
        raise RuntimeError("gh CLI is not available")
    result = run_command(
        ["gh", "api", "-X", "POST", path, "--input", "-"],
        cwd=repo_root,
        input_text=json.dumps(payload, ensure_ascii=False),
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "gh api post failed").strip())
    return json.loads(result.stdout) if result.stdout.strip() else {}


def github_post_via_rest(path: str, payload: dict[str, object]) -> object:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN or GH_TOKEN is required for REST comment posting")
    url = f"https://api.github.com{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "aiTrade-local-agent",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub REST API post failed ({exc.code}): {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"GitHub REST API post failed: {exc.reason}") from exc
    return json.loads(body) if body.strip() else {}


def github_post(path: str, payload: dict[str, object], repo_root: Path) -> tuple[object, str]:
    gh_error: str | None = None
    try:
        return github_post_via_gh(path, payload, repo_root), "gh"
    except Exception as exc:
        gh_error = str(exc)
    try:
        return github_post_via_rest(path, payload), "rest"
    except Exception as exc:
        raise RuntimeError(f"GitHub issue comment post failed. gh: {gh_error}; rest: {exc}") from exc


def github_api(path: str, repo_root: Path) -> tuple[object, str]:
    gh_error: str | None = None
    try:
        return github_api_via_gh(path, repo_root), "gh"
    except Exception as exc:
        gh_error = str(exc)
    try:
        return github_api_via_rest(path), "rest"
    except Exception as exc:
        raise RuntimeError(f"GitHub issue fetch failed. gh: {gh_error}; rest: {exc}") from exc


def find_marker_text(text: str, markers: Iterable[str] = ISSUE_TASK_MARKERS) -> tuple[str | None, str | None]:
    if not text:
        return None, None
    clean_text = text.lstrip("\ufeff")
    best_marker: str | None = None
    best_index: int | None = None
    for marker in markers:
        index = clean_text.find(marker)
        if index >= 0 and (best_index is None or index < best_index):
            best_marker = marker
            best_index = index
    if best_marker is None or best_index is None:
        return None, None
    return best_marker, clean_text[best_index:]


def parse_github_datetime(value: object) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.min.replace(tzinfo=timezone.utc)


def select_issue_task(issue: dict[str, object], comments: list[dict[str, object]]) -> tuple[str, str, str]:
    newest_comments = sorted(
        comments,
        key=lambda comment: parse_github_datetime(comment.get("created_at") or comment.get("updated_at")),
        reverse=True,
    )
    body = str(issue.get("body") or "")
    for marker in ISSUE_TASK_MARKERS:
        for comment in newest_comments:
            found_marker, task = find_marker_text(str(comment.get("body") or ""), (marker,))
            if task:
                return task, found_marker or marker, "issue_comment"
        found_marker, task = find_marker_text(body, (marker,))
        if task:
            return task, found_marker or marker, "issue_body"
    raise ValueError(
        "No task marker found in GitHub issue or comments. Expected one of: "
        + ", ".join(ISSUE_TASK_MARKERS)
    )


def fetch_issue_task(
    *,
    repo_root: Path,
    repo: str,
    issue_number: int,
    issue_comment_latest: bool,
) -> tuple[str, dict[str, object]]:
    owner_repo = repo.strip() or DEFAULT_ISSUE_REPO
    issue_path = f"/repos/{owner_repo}/issues/{issue_number}"
    comments_path = f"/repos/{owner_repo}/issues/{issue_number}/comments"
    issue_obj, issue_method = github_api(issue_path, repo_root)
    comments_obj, comments_method = github_api(comments_path, repo_root)
    if not isinstance(issue_obj, dict):
        raise RuntimeError("GitHub issue response was not an object")
    if not isinstance(comments_obj, list):
        raise RuntimeError("GitHub comments response was not a list")
    comments = [comment for comment in comments_obj if isinstance(comment, dict)]
    task, marker, source = select_issue_task(issue_obj, comments)
    context = {
        "task_source": source,
        "issue_number": issue_number,
        "issue_url": issue_obj.get("html_url"),
        "task_marker": marker,
        "task_fetched_at": utc_now_iso(),
        "issue_repo": owner_repo,
        "issue_title": issue_obj.get("title"),
        "issue_fetch_method": issue_method if issue_method == comments_method else f"{issue_method},{comments_method}",
        "issue_comment_latest": bool(issue_comment_latest),
    }
    return task, context


def extract_allowed_directives(prompt: str, repo_root: Path) -> tuple[set[str], list[str]]:
    allowed_files: set[str] = set()
    allowed_globs: list[str] = []
    for line in prompt.splitlines():
        stripped = line.lstrip("\ufeff").strip()
        if stripped.startswith(ALLOWED_FILE_PREFIX):
            path_text = stripped[len(ALLOWED_FILE_PREFIX) :].strip()
            if path_text:
                allowed_files.add(normalize_repo_path(path_text, repo_root))
        elif stripped.startswith(ALLOWED_GLOB_PREFIX):
            pattern = stripped[len(ALLOWED_GLOB_PREFIX) :].strip()
            if pattern:
                allowed_globs.append(normalize_repo_path(pattern, repo_root))
    return allowed_files, allowed_globs


def print_last_result(repo_root: Path) -> int:
    result_path = repo_root / RESULT_RELATIVE_PATH
    if not result_path.exists():
        print(f"No aiTrade Local Agent result found: {result_path}")
        print("Run scripts\\run_aitrade_agent_once.cmd first, or run tools\\aitrade_agent.py with a task.")
        return 1
    result = json.loads(read_text_file(result_path))
    print_summary(result, result_path)
    stdout = str(result.get("codex_stdout") or "")
    if stdout:
        print("codex_stdout_excerpt:")
        print(stdout[:2000])
        if len(stdout) > 2000:
            print("... truncated ...")
    return 0


def truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n... truncated {len(text) - max_chars} chars ..."


def markdown_list(values: object) -> str:
    if not isinstance(values, list) or not values:
        return "- 없음"
    return "\n".join(f"- `{value}`" for value in values)


def build_issue_report_body(result: dict[str, object]) -> str:
    stdout = truncate_text(str(result.get("codex_stdout") or "").strip(), 24000)
    stderr = truncate_text(str(result.get("codex_stderr") or "").strip(), 8000)
    result_json = truncate_text(json.dumps(result, ensure_ascii=False, indent=2), 24000)
    lines = [
        "## aiTrade Local Agent Report",
        "",
        f"- summary: `{result.get('summary')}`",
        f"- safe: `{result.get('safe')}`",
        f"- branch: `{result.get('branch')}`",
        f"- codex_exit_code: `{result.get('codex_exit_code')}`",
        f"- result_json: `{RESULT_RELATIVE_PATH.as_posix()}`",
        f"- reported_at: `{utc_now_iso()}`",
        "",
        "### Changed files",
        markdown_list(result.get("changed_files")),
        "",
        "### Violations",
        markdown_list(result.get("violations")),
    ]
    if stdout:
        lines.extend(
            [
                "",
                "<details>",
                "<summary>codex_stdout</summary>",
                "",
                "```text",
                stdout,
                "```",
                "</details>",
            ]
        )
    if stderr:
        lines.extend(
            [
                "",
                "<details>",
                "<summary>codex_stderr</summary>",
                "",
                "```text",
                stderr,
                "```",
                "</details>",
            ]
        )
    lines.extend(
        [
            "",
            "<details>",
            "<summary>result_json</summary>",
            "",
            "```json",
            result_json,
            "```",
            "</details>",
        ]
    )
    return truncate_text("\n".join(lines).strip() + "\n", 62000)


def report_issue(repo_root: Path, issue_number: int, repo: str, dry_run: bool) -> int:
    result_path = repo_root / RESULT_RELATIVE_PATH
    if not result_path.exists():
        raise FileNotFoundError(f"No aiTrade Local Agent result found: {result_path}")
    result = json.loads(read_text_file(result_path))
    body = build_issue_report_body(result)
    owner_repo = repo.strip() or DEFAULT_ISSUE_REPO
    result.update(
        {
            "report_issue_number": issue_number,
            "report_issue_repo": owner_repo,
            "report_issue_url": f"https://github.com/{owner_repo}/issues/{issue_number}",
            "report_dry_run": dry_run,
            "report_body_chars": len(body),
        }
    )
    if dry_run:
        result["summary"] = "report_dry_run_ok"
        print_summary(result, result_path)
        print("report_body:")
        print(body)
        return 0

    comment_path = f"/repos/{owner_repo}/issues/{issue_number}/comments"
    comment_obj, method = github_post(comment_path, {"body": body}, repo_root)
    comment_url = comment_obj.get("html_url") if isinstance(comment_obj, dict) else None
    result["report_method"] = method
    result["report_comment_url"] = comment_url
    result["summary"] = "report_issue_ok"
    print_summary(result, result_path)
    return 0



def maybe_report_after_run(
    repo_root: Path,
    result_path: Path,
    args: argparse.Namespace,
    exit_code: int,
) -> int:
    if args.report_issue is None:
        return exit_code
    try:
        report_code = report_issue(
            repo_root,
            args.report_issue,
            args.repo or DEFAULT_ISSUE_REPO,
            args.report_dry_run,
        )
    except Exception as exc:
        print(f"agent_error: report after run failed: {exc}", file=sys.stderr)
        return 1
    return exit_code if exit_code != 0 else report_code


def evaluate_policy(
    changed_files: list[str],
    allowed_files: set[str],
    allowed_globs: list[str],
    codex_exit_code: int | None,
) -> tuple[list[str], bool, str]:
    result_path = RESULT_RELATIVE_PATH.as_posix()
    policy_changed_files = [path for path in changed_files if path != result_path]
    if not allowed_files and not allowed_globs and policy_changed_files:
        return [], False, "needs_review: changed files exist but no allowed-file or allowed-glob was provided"

    violations = [
        path for path in policy_changed_files if not is_allowed(path, allowed_files, allowed_globs)
    ]
    if violations:
        return violations, False, "policy_violation: changed files outside allowed paths"
    if codex_exit_code not in (None, 0):
        return [], False, "codex_failed: Codex CLI returned a non-zero exit code"
    return [], True, "ok"


def self_test(repo_root: Path) -> int:
    pre_status = git_status(repo_root)
    branch = git_branch(repo_root)
    checks = {
        "git": shutil.which("git") is not None,
        "codex": shutil.which("codex") is not None,
        "repo_root": repo_root.exists(),
    }
    post_status = git_status(repo_root)
    changed_files = status_changed_files(post_status)
    safe = all(checks.values())
    failed = [name for name, ok in checks.items() if not ok]
    summary = "self_test_ok" if safe else f"self_test_failed: {', '.join(failed)}"
    result = build_result(
        repo_root=repo_root,
        branch=branch,
        pre_status=pre_status,
        post_status=post_status,
        changed_files=changed_files,
        violations=failed,
        codex_exit_code=None,
        safe=safe,
        summary=summary,
    )
    result_path = write_result(repo_root, result)
    print_summary(result, result_path)
    return 0 if safe else 1


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Codex CLI with aiTrade local checks.")
    parser.add_argument("--prompt", help="Prompt text to pass to codex exec.")
    parser.add_argument("--prompt-file", help="UTF-8 prompt file path.")
    parser.add_argument("--allowed-file", action="append", default=[], help="Allowed changed file.")
    parser.add_argument("--allowed-glob", action="append", default=[], help="Allowed changed glob.")
    parser.add_argument("--goal", help="Short goal text. The agent generates a standard task prompt.")
    parser.add_argument(
        "--goal-profile",
        default="readonly",
        help="Goal profile: readonly, agent, price-lane, or stockboard-ui. Defaults to readonly.",
    )
    parser.add_argument(
        "--goal-draft-only",
        action="store_true",
        help="Save the generated goal task preview without running Codex CLI.",
    )
    parser.add_argument("--issue", type=int, help="Fetch prompt text from a GitHub issue.")
    parser.add_argument("--repo", help="GitHub repository in owner/name form. Defaults to origin or aiTrade.")
    parser.add_argument(
        "--issue-comment-latest",
        action="store_true",
        help="Use the latest issue comments first when selecting a marked task.",
    )
    parser.add_argument(
        "--issue-fetch-only",
        action="store_true",
        help="Fetch and validate the GitHub issue task without running Codex CLI.",
    )
    parser.add_argument("--self-test", action="store_true", help="Check local prerequisites only.")
    parser.add_argument("--show-result", action="store_true", help="Show the last result JSON summary.")
    parser.add_argument("--report-issue", type=int, help="Post the current result JSON as a GitHub issue comment.")
    parser.add_argument(
        "--report-dry-run",
        action="store_true",
        help="Build and print the issue report body without posting a GitHub comment.",
    )
    return parser.parse_args(argv)


def build_error_result(
    *,
    repo_root: Path,
    exc: Exception,
    issue_context: dict[str, object] | None = None,
    task_context: dict[str, object] | None = None,
) -> dict[str, object]:
    branch = git_branch(repo_root)
    pre_status = git_status(repo_root)
    post_status = git_status(repo_root)
    changed_files = status_changed_files(post_status)
    return build_result(
        repo_root=repo_root,
        branch=branch,
        pre_status=pre_status,
        post_status=post_status,
        changed_files=changed_files,
        violations=[str(exc)],
        codex_exit_code=None,
        safe=False,
        summary=f"agent_error: {exc}",
        issue_context=issue_context,
        task_context=task_context,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    task_context: dict[str, object] = {}
    try:
        repo_root = find_repo_root()
        if args.show_result:
            return print_last_result(repo_root)
        run_requested = any(
            (
                args.goal,
                args.issue is not None,
                args.prompt,
                args.prompt_file,
                args.self_test,
                args.goal_draft_only,
                args.issue_fetch_only,
            )
        )
        if args.report_issue is not None and not run_requested:
            return report_issue(
                repo_root,
                args.report_issue,
                args.repo or DEFAULT_ISSUE_REPO,
                args.report_dry_run,
            )
        if args.report_dry_run and args.report_issue is None:
            raise ValueError("--report-dry-run requires --report-issue")
        if args.self_test:
            code = self_test(repo_root)
            return maybe_report_after_run(repo_root, repo_root / RESULT_RELATIVE_PATH, args, code)
        if args.issue_fetch_only and args.issue is None:
            raise ValueError("--issue-fetch-only requires --issue")
        if args.goal_draft_only and not args.goal:
            raise ValueError("--goal-draft-only requires --goal")
        if args.goal and args.issue is not None:
            raise ValueError("--goal cannot be combined with --issue")

        issue_context: dict[str, object] = {}
        if args.goal:
            prompt, task_context = build_goal_task(args.goal, args.goal_profile, repo_root)
        elif args.issue is not None:
            issue_repo = args.repo or infer_issue_repo(repo_root)
            try:
                prompt, issue_context = fetch_issue_task(
                    repo_root=repo_root,
                    repo=issue_repo,
                    issue_number=args.issue,
                    issue_comment_latest=args.issue_comment_latest,
                )
            except Exception as exc:
                issue_context = {
                    "task_source": "github_issue",
                    "issue_number": args.issue,
                    "issue_url": None,
                    "task_marker": None,
                    "task_fetched_at": utc_now_iso(),
                    "issue_repo": issue_repo,
                    "issue_title": None,
                }
                result = build_error_result(
                    repo_root=repo_root,
                    exc=exc,
                    issue_context=issue_context,
                    task_context=task_context,
                )
                result_path = write_result(repo_root, result)
                print_summary(result, result_path)
                return 1
        else:
            prompt = read_prompt(args, repo_root)

        prompt_allowed_files, prompt_allowed_globs = extract_allowed_directives(prompt, repo_root)
        allowed_files = prompt_allowed_files | {
            normalize_repo_path(path, repo_root) for path in args.allowed_file if path.strip()
        }
        allowed_globs = prompt_allowed_globs + [
            normalize_repo_path(pattern, repo_root) for pattern in args.allowed_glob if pattern.strip()
        ]
        pre_status = git_status(repo_root)
        branch = git_branch(repo_root)
        if args.goal_draft_only:
            post_status = git_status(repo_root)
            changed_files = status_changed_files(post_status)
            draft_changed_files = status_changed_files(status_lines_added_after(pre_status, post_status))
            violations, safe, summary = evaluate_policy(
                draft_changed_files,
                allowed_files,
                allowed_globs,
                None,
            )
            if safe:
                summary = "goal_draft_ok"
            result = build_result(
                repo_root=repo_root,
                branch=branch,
                pre_status=pre_status,
                post_status=post_status,
                changed_files=changed_files,
                violations=violations,
                codex_exit_code=None,
                safe=safe,
                summary=summary,
                issue_context=issue_context,
                task_context=task_context,
            )
            result_path = write_result(repo_root, result)
            print_summary(result, result_path)
            return maybe_report_after_run(repo_root, result_path, args, 0 if safe else 1)
        if args.issue_fetch_only:
            post_status = git_status(repo_root)
            changed_files = status_changed_files(post_status)
            fetch_only_changed_files = status_changed_files(status_lines_added_after(pre_status, post_status))
            violations, safe, summary = evaluate_policy(
                fetch_only_changed_files,
                allowed_files,
                allowed_globs,
                None,
            )
            if safe:
                summary = "issue_fetch_ok"
            result = build_result(
                repo_root=repo_root,
                branch=branch,
                pre_status=pre_status,
                post_status=post_status,
                changed_files=changed_files,
                violations=violations,
                codex_exit_code=None,
                safe=safe,
                summary=summary,
                issue_context=issue_context,
                task_context=task_context,
            )
            result_path = write_result(repo_root, result)
            print_summary(result, result_path)
            return maybe_report_after_run(repo_root, result_path, args, 0 if safe else 1)

        codex_args = [
            "codex",
            "-C",
            str(repo_root),
            "-s",
            "danger-full-access",
            "-a",
            "never",
            "exec",
            prompt,
        ]
        codex_result = run_command(codex_args, cwd=repo_root)
        post_status = git_status(repo_root)
        changed_files = status_changed_files(post_status)
        violations, safe, summary = evaluate_policy(
            changed_files,
            allowed_files,
            allowed_globs,
            codex_result.returncode,
        )
        result = build_result(
            repo_root=repo_root,
            branch=branch,
            pre_status=pre_status,
            post_status=post_status,
            changed_files=changed_files,
            violations=violations,
            codex_exit_code=codex_result.returncode,
            safe=safe,
            summary=summary,
            issue_context=issue_context,
            task_context=task_context,
        )
        result["codex_stdout"] = codex_result.stdout
        result["codex_stderr"] = codex_result.stderr
        result_path = write_result(repo_root, result)
        print_summary(result, result_path)
        return maybe_report_after_run(repo_root, result_path, args, 0 if safe else 1)
    except Exception as exc:
        try:
            repo_root = find_repo_root()
            result = build_error_result(repo_root=repo_root, exc=exc, task_context=task_context)
            result_path = write_result(repo_root, result)
            print_summary(result, result_path)
            return maybe_report_after_run(repo_root, result_path, args, 1)
        except Exception:
            print(f"agent_error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
