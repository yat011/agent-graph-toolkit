"""Shared dispatch/executor module.

Dispatches one Worker per call via the process Worker CLI (Claude Code `claude`, Grok Build
`grok`, or Cursor Agent `cursor-agent` — see `worker_cli.py`). Every dispatch is stateless:
this module never passes `--resume`/`--continue`, and never tracks/reuses a `session_id`
across calls — a Node's own file-based context (paths passed in its prompt) plus the Graph's
checkpointer state are the only continuity mechanism across attempts.

No provider API key is used or read anywhere in this module. The Executor (the `executor`
callable) is the pluggable in-process seam: swapping the real subprocess for a test fake
never requires changing a Node/Graph. The vendor binary is selected by `current_worker_cli()`,
not by a `cli=` argument on this function or on any node.

A failing/erroring CLI call is an ordinary technical failure, surfaced via
`DispatchResult.ok is False`, subject to each Node's own `retry` count via `dispatch_with_retry`.
Claude and Cursor receive the combined prompt on stdin; Grok's `-p` requires that same
prompt as the option value (stdin is still populated for the executor seam).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from agentgraph_engine import constants as constants_mod
from agentgraph_engine.constants import ROLE_GENERAL_PURPOSE, USAGE_KEY
from agentgraph_engine.worker_cli import (
    build_usage,
    current_worker_cli,
    worker_cli_for,
)

# Optional ATX heading (`## Result:`) plus case-insensitive `Result:` so workers that write
# `## Result: ACCEPTED` still yield a phrase classify_gate can match.
RESULT_LINE_RE = re.compile(r"(?im)^(?:#{1,6}\s+)?Result:\s*(.+?)\s*$")

# agents/{role}.md lives two levels up from this file (repo_root/agentgraph_engine/dispatch.py).
AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"

USAGE_FILENAME = "usage.json"

# Last prompt line. Test fakes parse the output path off this prefix; keep it last.
OUTPUT_PATH_LINE_PREFIX = "Write your required output to this exact file path before finishing: "

# A role with no agents/{role}.md file (e.g. "general-purpose") gets no persona text prepended —
# the task prompt is dispatched as-is.
NO_PERSONA_ROLES = {ROLE_GENERAL_PURPOSE}


Executor = Callable[[list, str, Optional[int]], subprocess.CompletedProcess]


@dataclass
class DispatchResult:
    ok: bool
    result_text: Optional[str]
    result_line: Optional[str]
    session_id: Optional[str]
    cost_usd: Optional[float]
    output_path: Path
    output_exists: bool
    exit_code: int
    stderr: str
    raw_envelope: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)


class RolePromptError(Exception):
    """Named role is missing a non-empty agents/{role}.md prompt."""


_ROLE_STRING_RE = re.compile(r"""role\s*=\s*(['"])(.*?)\1""")
_ROLE_CONST_RE = re.compile(r"role\s*=\s*(ROLE_[A-Z0-9_]+)")
_NESTED_GRAPH_RE = re.compile(
    r"Path\(__file__\)(?:\.resolve\(\))?(?P<parents>(?:\.parent)+)\s*/\s*"
    r"""['"](?P<dir>[^'"]+)['"]\s*/\s*['"]graph\.py['"]"""
)


def load_role_prompt(role: str) -> str:
    """Read agents/{role}.md (frontmatter stripped) for combining into a headless prompt.

    `general-purpose` (and other NO_PERSONA_ROLES) returns "" by design.
    Any other named role raises RolePromptError if the file is missing or empty
    after frontmatter strip.
    """
    if role in NO_PERSONA_ROLES:
        return ""
    path = AGENTS_DIR / f"{role}.md"
    if not path.exists():
        raise RolePromptError(f"Missing role prompt for {role!r}: {path} does not exist")
    body = path.read_text(encoding="utf-8")
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            body = body[end + 4 :]
    body = body.strip()
    if not body:
        raise RolePromptError(
            f"Empty role prompt for {role!r}: {path} has no body after frontmatter"
        )
    return body


def scan_roles_in_source(source: str) -> list[str]:
    """Return role names referenced as role="..." / role='...' or role=ROLE_*."""
    found: list[str] = []
    for _quote, role in _ROLE_STRING_RE.findall(source):
        found.append(role)
    for const_name in _ROLE_CONST_RE.findall(source):
        value = getattr(constants_mod, const_name, None)
        if isinstance(value, str):
            found.append(value)
    return found


def _nested_graph_paths(source_file: Path, source: str) -> list[Path]:
    """Resolve Path(__file__).parent... / "<dir>" / "graph.py" literals next to source_file."""
    found: list[Path] = []
    for match in _NESTED_GRAPH_RE.finditer(source):
        base = source_file
        for _ in range(match.group("parents").count(".parent")):
            base = base.parent
        candidate = (base / match.group("dir") / "graph.py").resolve()
        if candidate.exists():
            found.append(candidate)
    return found


def required_roles_from_graph_path(graph_py: Path) -> list[str]:
    """Named roles used by this graph, its sibling nodes.py, and nested graph.py paths it loads."""
    pending = [graph_py.resolve()]
    seen_graphs: set[Path] = set()
    roles: list[str] = []
    seen_roles: set[str] = set()
    while pending:
        current = pending.pop()
        if current in seen_graphs:
            continue
        seen_graphs.add(current)
        files = [current]
        sibling = current.parent / "nodes.py"
        if sibling.exists():
            files.append(sibling)
        for file_path in files:
            source = file_path.read_text(encoding="utf-8")
            for nested in _nested_graph_paths(file_path, source):
                pending.append(nested)
            for role in scan_roles_in_source(source):
                if role in NO_PERSONA_ROLES or role in seen_roles:
                    continue
                seen_roles.add(role)
                roles.append(role)
    return roles


def preflight_role_prompts(graph_py: Path) -> None:
    """Raise RolePromptError if any named role used by the graph lacks a prompt file."""
    for role in required_roles_from_graph_path(graph_py):
        load_role_prompt(role)


def extract_result_line(text: Optional[str]) -> Optional[str]:
    """Extract the last `Result: <phrase>` line's phrase (prefix stripped), or None."""
    if not text:
        return None
    matches = RESULT_LINE_RE.findall(text)
    return matches[-1].strip() if matches else None


def attach_usage(record: dict, result: DispatchResult) -> None:
    """Copy the per-dispatch usage object onto a nested node record."""
    record[USAGE_KEY] = result.usage


def _run_subprocess(argv: list, input_text: str, timeout: Optional[int]) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, input=input_text, capture_output=True, text=True, timeout=timeout, encoding="utf-8"
    )


def _write_usage_json(output_path: Path, usage: dict) -> None:
    usage_path = output_path.parent / USAGE_FILENAME
    usage_path.write_text(json.dumps(usage), encoding="utf-8")


def dispatch_worker(
    *,
    role: str,
    task_prompt: str,
    output_path: Path | str,
    model: Optional[str] = None,
    timeout: Optional[int] = 1800,
    executor: Optional[Executor] = None,
) -> DispatchResult:
    """Dispatch one stateless Worker call.

    The combined prompt is: the role's persona text (`agents/{role}.md`, frontmatter stripped) +
    the node's own `task_prompt` + caveman-full output.md voice (Result line minimum, never
    recap, file:line pointers) + an instruction to write that output to `output_path`. A
    fresh headless CLI process has no notion of a Claude-Code "subagent type",
    so this is how a Node's declared `agent:` role is carried into the dispatch without inventing
    an undocumented CLI flag.

    `output_path` not existing after the call (worker crashed, or simply didn't write it) makes
    `ok=False` — an ordinary technical failure, same as a non-zero exit code.
    """
    executor = executor or _run_subprocess
    persona = load_role_prompt(role)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    identity = current_worker_cli()
    vendor_cli = worker_cli_for(identity)
    mapped_model = vendor_cli.resolve_model(model)

    parts = []
    parts.append(
        "Always use caveman skill full mode. "
        "output.md minimum: one-line `Result: {xxx}` plus this node's extra required lines only. "
        "Never recap. Cite file:line pointers instead of restated content. "
        "Keep Result: line, paths, commands, code, and error strings exact.\n"
        "Before you finish, MUST use file-write tool to create the file below with that "
        "output as its content — chat reply alone not enough, next step finds work from this file.\n"
    )
    if persona:
        parts.append(persona)
    parts.append(task_prompt.strip())
    # One place for every LLM node: output.md voice. Path line stays last so test fakes can parse it.
    parts.append(f"{OUTPUT_PATH_LINE_PREFIX}{output_path}")

    combined_prompt = "\n\n---\n\n".join(parts) + "\n"

    # Resolve via PATH/PATHEXT (shutil.which) rather than passing the binary name straight to
    # subprocess: on Windows, CLIs are often installed as `.cmd` shims, and CreateProcess
    # (what subprocess uses with shell=False) will not find them from the bare name the way a
    # shell's PATH lookup would. Falls back to the raw name if not found, so a genuinely missing
    # CLI still surfaces as an ordinary technical failure rather than a different kind of crash.
    resolved_binary = shutil.which(vendor_cli.binary) or vendor_cli.binary
    argv = vendor_cli.argv(resolved_binary, mapped_model, combined_prompt)

    try:
        proc = executor(argv, combined_prompt, timeout)
    except Exception as exc:  # subprocess couldn't start, timed out, etc. — technical failure
        usage = build_usage(identity=identity, mapped_model=mapped_model, envelope={})
        _write_usage_json(output_path, usage)
        return DispatchResult(
            ok=False,
            result_text=None,
            result_line=None,
            session_id=None,
            cost_usd=None,
            output_path=output_path,
            output_exists=output_path.exists(),
            exit_code=-1,
            stderr=str(exc),
            usage=usage,
        )

    envelope: dict = {}
    result_text: Optional[str] = None
    session_id: Optional[str] = None
    stdout = proc.stdout or ""
    if stdout.strip():
        try:
            envelope = json.loads(stdout)
            result_text = envelope.get("result")
            session_id = envelope.get("session_id")
        except json.JSONDecodeError:
            result_text = stdout

    usage = build_usage(identity=identity, mapped_model=mapped_model, envelope=envelope)
    _write_usage_json(output_path, usage)

    output_exists = output_path.exists()
    result_line = None
    if output_exists:
        result_line = extract_result_line(output_path.read_text(encoding="utf-8"))
    if result_line is None:
        result_line = extract_result_line(result_text)

    ok = (proc.returncode == 0) and output_exists
    return DispatchResult(
        ok=ok,
        result_text=result_text,
        result_line=result_line,
        session_id=session_id,
        cost_usd=usage.get("cost_usd"),
        output_path=output_path,
        output_exists=output_exists,
        exit_code=proc.returncode,
        stderr=proc.stderr or "",
        raw_envelope=envelope,
        usage=usage,
    )


def dispatch_with_retry(*, retry: int = 0, **kwargs) -> DispatchResult:
    """Call `dispatch_worker` up to `retry + 1` times, stopping at the first `ok` result.

    Mirrors the old engine's `retry` semantics exactly: technical-failure retries only, never
    branch-driven loop-backs (those are a separate, node-specific counter tracked in graph state).
    If every attempt fails, the last (failing) `DispatchResult` is returned so the caller can
    route to its halted terminal — this function never halts by itself.
    """
    attempts = max(retry, 0) + 1
    result: Optional[DispatchResult] = None
    for _ in range(attempts):
        result = dispatch_worker(**kwargs)
        if result.ok:
            return result
    assert result is not None
    return result
