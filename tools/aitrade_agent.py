#!/usr/bin/env python3
"""Minimal local runner for Codex CLI with repository policy checks."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


RESULT_RELATIVE_PATH = Path("data/runtime/aitrade_agent_result.json")


def run_command(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


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
) -> dict[str, object]:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
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
    print(f"changed_files: {len(result['changed_files'])}")
    print(f"violations: {len(result['violations'])}")
    print(f"codex_exit_code: {result['codex_exit_code']}")
    print(f"result_json: {result_path}")


def read_prompt(args: argparse.Namespace, repo_root: Path) -> str:
    prompt_parts: list[str] = []
    if args.prompt:
        prompt_parts.append(args.prompt)
    if args.prompt_file:
        prompt_path = Path(args.prompt_file)
        if not prompt_path.is_absolute():
            prompt_path = repo_root / prompt_path
        prompt_parts.append(prompt_path.read_text(encoding="utf-8", errors="replace"))
    prompt = "\n\n".join(part.strip() for part in prompt_parts if part and part.strip())
    if not prompt:
        raise ValueError("--prompt or --prompt-file is required unless --self-test is used")
    return prompt


def evaluate_policy(
    changed_files: list[str],
    allowed_files: set[str],
    allowed_globs: list[str],
    codex_exit_code: int | None,
) -> tuple[list[str], bool, str]:
    if not allowed_files and not allowed_globs and changed_files:
        return [], False, "needs_review: changed files exist but no allowed-file or allowed-glob was provided"

    violations = [
        path for path in changed_files if not is_allowed(path, allowed_files, allowed_globs)
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
    parser.add_argument("--self-test", action="store_true", help="Check local prerequisites only.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        repo_root = find_repo_root()
        if args.self_test:
            return self_test(repo_root)

        prompt = read_prompt(args, repo_root)
        allowed_files = {
            normalize_repo_path(path, repo_root) for path in args.allowed_file if path.strip()
        }
        allowed_globs = [
            normalize_repo_path(pattern, repo_root) for pattern in args.allowed_glob if pattern.strip()
        ]
        pre_status = git_status(repo_root)
        branch = git_branch(repo_root)
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
        )
        result["codex_stdout"] = codex_result.stdout
        result["codex_stderr"] = codex_result.stderr
        result_path = write_result(repo_root, result)
        print_summary(result, result_path)
        return 0 if safe else 1
    except Exception as exc:
        try:
            repo_root = find_repo_root()
            branch = git_branch(repo_root)
            pre_status = git_status(repo_root)
            post_status = git_status(repo_root)
            changed_files = status_changed_files(post_status)
            result = build_result(
                repo_root=repo_root,
                branch=branch,
                pre_status=pre_status,
                post_status=post_status,
                changed_files=changed_files,
                violations=[str(exc)],
                codex_exit_code=None,
                safe=False,
                summary=f"agent_error: {exc}",
            )
            result_path = write_result(repo_root, result)
            print_summary(result, result_path)
        except Exception:
            print(f"agent_error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
