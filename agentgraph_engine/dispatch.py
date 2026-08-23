"""Shared dispatch/executor module.

Dispatches one Worker per call via a headless CLI (`claude -p --permission-mode auto
--output-format json` by default; Haiku dispatches use `acceptEdits --allowedTools Write`
instead, since the `auto` action-classifier requires Sonnet-tier and above). Every dispatch is
stateless: this module never passes `--resume`/`--continue`, and never tracks/reuses a
`session_id` across calls — a Node's own file-based context (paths passed in its prompt) plus the
Graph's checkpointer state are the only continuity mechanism across attempts.

No provider API key is used or read anywhere in this module. The Executor (the `executor`
callable) is the pluggable piece: swapping `claude -p` for `kiro-cli` or `orca terminal send`
later is a new `executor`/`cli` value, never a change to this function's signature or to any
Node/Graph that calls it.

A failing/erroring CLI call is an ordinary technical failure, surfaced via
`DispatchResult.ok is False`, subject to each Node's own `retry` count via `dispatch_with_retry`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from agentgraph_engine.constants import MODEL_CHEAP, ROLE_GENERAL_PURPOSE

RESULT_LINE_RE = re.compile(r"(?m)^Result:\s*(.+?)\s*$")

# agents/{role}.md lives two levels up from this file (repo_root/agentgraph_engine/dispatch.py).
AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"

# `model: cheap` (and the legacy `haiku` alias) map to the CLI's cheapest model.
MODEL_ALIASES = {MODEL_CHEAP: "haiku", "haiku": "haiku"}

# A role with no agents/{role}.md file (e.g. "general-purpose") gets no persona text prepended —
# the task prompt is dispatched as-is.
NO_PERSONA_ROLES = {ROLE_GENERAL_PURPOSE}


def _permission_mode_args(resolved_model: Optional[str]) -> list:
    """`auto` requires the Sonnet-tier-and-above action-classifier; Haiku doesn't support it and
    denies every tool call in a headless context. Haiku dispatches use `acceptEdits` (no
    classifier needed) plus an explicit `Write` allowlist, since that's the one tool this module's
    contract actually requires (see module docstring)."""
    if resolved_model == "haiku":
        return ["--permission-mode", "acceptEdits", "--allowedTools", "Write"]
    return ["--permission-mode", "auto"]

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


def load_role_prompt(role: str) -> str:
    """Read agents/{role}.md (frontmatter stripped) for combining into a headless prompt.

    `role` with no corresponding file (e.g. "general-purpose") returns "".
    """
    if role in NO_PERSONA_ROLES:
        return ""
    path = AGENTS_DIR / f"{role}.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :]
    return text.strip()


def extract_result_line(text: Optional[str]) -> Optional[str]:
    """Extract the last `Result: <phrase>` line's phrase (prefix stripped), or None."""
    if not text:
        return None
    matches = RESULT_LINE_RE.findall(text)
    return matches[-1].strip() if matches else None


def _run_subprocess(argv: list, input_text: str, timeout: Optional[int]) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, input=input_text, capture_output=True, text=True, timeout=timeout, encoding="utf-8"
    )


def dispatch_worker(
    *,
    role: str,
    task_prompt: str,
    output_path: Path | str,
    model: Optional[str] = None,
    cli: str = "claude",
    timeout: Optional[int] = 1800,
    executor: Optional[Executor] = None,
) -> DispatchResult:
    """Dispatch one stateless Worker call.

    The combined prompt is: the role's persona text (`agents/{role}.md`, frontmatter stripped) +
    the node's own `task_prompt` + an instruction to write the node's full output to
    `output_path`. A fresh headless CLI process has no notion of a Claude-Code "subagent type",
    so this is how a Node's declared `agent:` role is carried into the dispatch without inventing
    an undocumented CLI flag.

    `output_path` not existing after the call (worker crashed, or simply didn't write it) makes
    `ok=False` — an ordinary technical failure, same as a non-zero exit code.
    """
    executor = executor or _run_subprocess
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    resolved_model = MODEL_ALIASES.get(model, model) if model else None
    persona = load_role_prompt(role)
    parts = []
    if persona:
        parts.append(persona)
    parts.append(task_prompt.strip())
    parts.append(
        "Before you finish, you MUST use your file-write tool to create the file below with your "
        "full output as its content — a chat reply alone is not enough, this file is how the next "
        "step finds your work.\n"
        f"Write your full output to this exact file path before finishing: {output_path}"
    )
    combined_prompt = "\n\n---\n\n".join(parts) + "\n"

    # Resolve via PATH/PATHEXT (shutil.which) rather than passing `cli` straight to subprocess:
    # on Windows, `claude` is installed as a `claude.cmd`/`claude.CMD` shim, and CreateProcess
    # (what subprocess uses with shell=False) will not find it from the bare name the way a
    # shell's PATH lookup would. Falls back to the raw name if not found, so a genuinely missing
    # CLI still surfaces as an ordinary technical failure rather than a different kind of crash.
    resolved_cli = shutil.which(cli) or cli
    argv = [resolved_cli, "-p", *_permission_mode_args(resolved_model), "--output-format", "json"]
    if resolved_model:
        argv += ["--model", resolved_model]

    try:
        proc = executor(argv, combined_prompt, timeout)
    except Exception as exc:  # subprocess couldn't start, timed out, etc. — technical failure
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
        )

    envelope: dict = {}
    result_text: Optional[str] = None
    session_id: Optional[str] = None
    cost_usd: Optional[float] = None
    stdout = proc.stdout or ""
    if stdout.strip():
        try:
            envelope = json.loads(stdout)
            result_text = envelope.get("result")
            session_id = envelope.get("session_id")
            cost_usd = envelope.get("total_cost_usd", envelope.get("cost_usd"))
        except json.JSONDecodeError:
            result_text = stdout

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
        cost_usd=cost_usd,
        output_path=output_path,
        output_exists=output_exists,
        exit_code=proc.returncode,
        stderr=proc.stderr or "",
        raw_envelope=envelope,
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
