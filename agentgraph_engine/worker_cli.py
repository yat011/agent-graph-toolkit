"""Worker CLI identities, once-per-process selection, and vendor argv/envelope implementations.

A Run dispatches every Worker through one Worker CLI — `claude`, `grok`, `cursor`
(binary `cursor-agent`), `grok-orca` (Grok TUI hosted in an Orca pane), or `muse`
(Muse Code `muse exec --json`). Selection
happens once per engine process (`--cli` > `~/.agents/agentgraph.json` > `claude`) and
lives in a ContextVar for the life of that process. `dispatch_worker` reads
`current_worker_cli()`; it does not re-run the cascade, and the checkpointed value is
not an input to the next process.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path
from typing import Protocol

from agentgraph_engine.constants import (
    MODEL_CHEAP,
    WORKER_CLI_CLAUDE,
    WORKER_CLI_CURSOR,
    WORKER_CLI_CURSOR_BINARY,
    WORKER_CLI_GROK,
    WORKER_CLI_GROK_ORCA,
    WORKER_CLI_IDENTITIES,
    WORKER_CLI_KEY,
    WORKER_CLI_MUSE,
)

GRAPH_MODEL_HAIKU = "haiku"
GRAPH_MODEL_SONNET = "sonnet"
GRAPH_MODEL_OPUS = "opus"

CLAUDE_MODEL_CHEAP = "haiku"
CLAUDE_MODEL_SONNET = "sonnet"
CLAUDE_MODEL_OPUS = "opus"
GROK_MODEL = "grok-4.6"
GROK_EFFORT = "high"
CURSOR_MODEL = "cursor-grok-4.6-high"
# Muse reasoning effort per graph model tier. `muse exec` takes no vendor model id for
# the Meta provider, so the tier maps onto its documented effort ladder instead; sonnet
# maps to the CLI default (`high`). Recorded as the usage `model` value, like the
# vendor model strings above.
MUSE_EFFORT_CHEAP = "low"
MUSE_EFFORT_SONNET = "high"
MUSE_EFFORT_OPUS = "max"

_resolved_worker_cli: ContextVar[str | None] = ContextVar("agentgraph_worker_cli", default=None)


class WorkerCliError(ValueError):
    """Unknown Worker CLI identity or unreadable settings file."""


class UnknownGraphModelError(ValueError):
    """Graph `model` is not cheap/haiku/sonnet/opus/unset."""


WorkerExecutor = Callable[[list[str], str, int | None], subprocess.CompletedProcess]

ORCA_BINARY = "orca"


class WorkerCli(Protocol):
    identity: str
    binary: str

    def resolve_model(self, graph_model: str | None) -> str: ...

    def argv(self, resolved_binary: str, mapped_model: str, prompt: str) -> list[str]: ...

    def parse_envelope(self, envelope: dict) -> dict: ...

    def run(
        self,
        resolved_binary: str,
        mapped_model: str,
        prompt: str,
        timeout: int | None,
        executor: WorkerExecutor,
        output_path: Path,
    ) -> subprocess.CompletedProcess: ...


def default_settings_path() -> Path:
    return Path.home() / ".agents" / "agentgraph.json"


def reset_worker_cli() -> None:
    """Clear the in-memory Worker CLI so the next `resolve_worker_cli` re-runs the cascade.

    Test helper — a production `agentgraph` process resolves once and never resets.
    """
    _resolved_worker_cli.set(None)


def current_worker_cli() -> str:
    """In-memory Worker CLI for this process.

    If `resolve_worker_cli` has never run, returns `claude` without reading the settings
    file so direct `dispatch_worker` calls keep today's default.
    """
    return _resolved_worker_cli.get() or WORKER_CLI_CLAUDE


def resolve_worker_cli(*, cli_flag: str | None = None, settings_path: Path | None = None) -> str:
    """Resolve Worker CLI once per process and store it in the ContextVar.

    Cascade: `--cli` (if present) > `worker_cli` in the settings file (if present) > `claude`.
    Idempotent within a process — the first call wins; later calls return the cached value
    and ignore a changed file or a different `cli_flag`.
    """
    cached = _resolved_worker_cli.get()
    if cached is not None:
        return cached
    selected = _select_worker_cli(cli_flag=cli_flag, settings_path=settings_path)
    _resolved_worker_cli.set(selected)
    return selected


def worker_cli_for(identity: str) -> WorkerCli:
    try:
        return _IMPLEMENTATIONS[identity]
    except KeyError as exc:
        raise WorkerCliError(
            f"unknown Worker CLI {identity!r}; expected one of {sorted(WORKER_CLI_IDENTITIES)}"
        ) from exc


def _select_worker_cli(*, cli_flag: str | None, settings_path: Path | None) -> str:
    if cli_flag is not None:
        return _require_legal(cli_flag, source="--cli")
    path = settings_path if settings_path is not None else default_settings_path()
    from_file = _read_settings_worker_cli(path)
    if from_file is not None:
        return from_file
    return WORKER_CLI_CLAUDE


def _require_legal(identity: str, *, source: str) -> str:
    if identity not in WORKER_CLI_IDENTITIES:
        raise WorkerCliError(
            f"unknown Worker CLI {identity!r} from {source}; "
            f"expected one of {sorted(WORKER_CLI_IDENTITIES)}"
        )
    return identity


def _read_settings_worker_cli(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkerCliError(f"could not read Worker CLI settings file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkerCliError(f"Worker CLI settings file {path} must contain a JSON object")
    value = data.get(WORKER_CLI_KEY)
    if value is None:
        return None
    if not isinstance(value, str):
        raise WorkerCliError(f"unknown Worker CLI {value!r} from {path}")
    return _require_legal(value, source=str(path))


def _graph_model_row(graph_model: str | None) -> str:
    """Map a graph `model=` value onto the cheap / sonnet / opus table row."""
    if graph_model is None:
        return GRAPH_MODEL_SONNET
    if graph_model in {MODEL_CHEAP, GRAPH_MODEL_HAIKU}:
        return MODEL_CHEAP
    if graph_model == GRAPH_MODEL_SONNET:
        return GRAPH_MODEL_SONNET
    if graph_model == GRAPH_MODEL_OPUS:
        return GRAPH_MODEL_OPUS
    raise UnknownGraphModelError(
        f"unknown graph model {graph_model!r}; expected cheap, haiku, sonnet, opus, or unset"
    )


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        as_float = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if as_float.is_integer():
        return int(as_float)
    return None


def _first(mapping: dict, *keys: str) -> object:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _claude_shaped_usage(envelope: dict) -> dict:
    usage_block = envelope.get("usage")
    if not isinstance(usage_block, dict):
        usage_block = {}
    cost = _first(envelope, "total_cost_usd", "cost_usd")
    if cost is None:
        cost = _first(usage_block, "total_cost_usd", "cost_usd")
    return {
        "cost_usd": _as_float(cost),
        "input_tokens": _as_int(usage_block.get("input_tokens")),
        "output_tokens": _as_int(usage_block.get("output_tokens")),
        "cache_read_tokens": _as_int(
            _first(usage_block, "cache_read_tokens", "cache_read_input_tokens")
        ),
        "cache_write_tokens": _as_int(
            _first(usage_block, "cache_write_tokens", "cache_creation_input_tokens")
        ),
    }


def _null_usage() -> dict:
    return {
        "cost_usd": None,
        "input_tokens": None,
        "output_tokens": None,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
    }


class ClaudeWorkerCli:
    identity = WORKER_CLI_CLAUDE
    binary = WORKER_CLI_CLAUDE

    def resolve_model(self, graph_model: str | None) -> str:
        row = _graph_model_row(graph_model)
        return {
            MODEL_CHEAP: CLAUDE_MODEL_CHEAP,
            GRAPH_MODEL_SONNET: CLAUDE_MODEL_SONNET,
            GRAPH_MODEL_OPUS: CLAUDE_MODEL_OPUS,
        }[row]

    def argv(self, resolved_binary: str, mapped_model: str, _prompt: str) -> list[str]:
        if mapped_model == CLAUDE_MODEL_CHEAP:
            permission = ["--permission-mode", "acceptEdits", "--allowedTools", "Write"]
        else:
            permission = ["--permission-mode", "auto"]
        return [
            resolved_binary,
            "-p",
            *permission,
            "--output-format",
            "json",
            "--exclude-dynamic-system-prompt-sections",
            "--model",
            mapped_model,
        ]

    def parse_envelope(self, envelope: dict) -> dict:
        return _claude_shaped_usage(envelope)

    def run(
        self,
        resolved_binary: str,
        mapped_model: str,
        prompt: str,
        timeout: int | None,
        executor: WorkerExecutor,
        output_path: Path,
    ) -> subprocess.CompletedProcess:
        return executor(self.argv(resolved_binary, mapped_model, prompt), prompt, timeout)


class GrokWorkerCli:
    identity = WORKER_CLI_GROK
    binary = WORKER_CLI_GROK

    def resolve_model(self, graph_model: str | None) -> str:
        _graph_model_row(graph_model)
        return GROK_MODEL

    def argv(self, resolved_binary: str, mapped_model: str, prompt: str) -> list[str]:
        # Grok's `-p` is `--single <PROMPT>` and must be followed by the prompt text.
        # A bare `-p --permission-mode ...` exits 2: "a value is required for '--single <PROMPT>'".
        return [
            resolved_binary,
            "-p",
            prompt,
            "--permission-mode",
            "auto",
            "--output-format",
            "json",
            "--model",
            mapped_model,
            "--effort",
            GROK_EFFORT,
        ]

    def parse_envelope(self, envelope: dict) -> dict:
        return _claude_shaped_usage(envelope)

    def run(
        self,
        resolved_binary: str,
        mapped_model: str,
        prompt: str,
        timeout: int | None,
        executor: WorkerExecutor,
        output_path: Path,
    ) -> subprocess.CompletedProcess:
        return executor(self.argv(resolved_binary, mapped_model, prompt), prompt, timeout)


class CursorWorkerCli:
    identity = WORKER_CLI_CURSOR
    binary = WORKER_CLI_CURSOR_BINARY

    def resolve_model(self, graph_model: str | None) -> str:
        _graph_model_row(graph_model)
        return CURSOR_MODEL

    def argv(self, resolved_binary: str, mapped_model: str, _prompt: str) -> list[str]:
        return [
            resolved_binary,
            "-p",
            "--auto-review",
            "--approve-mcps",
            "--trust",
            "--output-format",
            "json",
            "--model",
            mapped_model,
        ]

    def parse_envelope(self, envelope: dict) -> dict:
        return _null_usage()

    def run(
        self,
        resolved_binary: str,
        mapped_model: str,
        prompt: str,
        timeout: int | None,
        executor: WorkerExecutor,
        output_path: Path,
    ) -> subprocess.CompletedProcess:
        return executor(self.argv(resolved_binary, mapped_model, prompt), prompt, timeout)


def _muse_terminal_result(stdout: str) -> tuple[str | None, str | None]:
    """Scan `muse exec --json` JSONL stdout for the terminal result text and session id.

    Returns `(text, session_id)`; each is None when its event is absent. A `(None,
    None)` return means stdout is not muse JSONL at all (e.g. a single-object test
    envelope), and the caller must leave the process output untouched.
    """
    text: str | None = None
    session_id: str | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if session_id is None:
            stream = event.get("stream")
            if isinstance(stream, dict) and stream.get("kind") == "session":
                candidate = stream.get("id")
                if isinstance(candidate, str) and candidate:
                    session_id = candidate
        payload = event.get("payload")
        if isinstance(payload, dict) and payload.get("kind") == "run_terminal":
            candidate = payload.get("text")
            if isinstance(candidate, str):
                text = candidate
    return text, session_id


class MuseWorkerCli:
    """Worker CLI identity: Muse Code headless `muse exec --json` with a positional prompt."""

    identity = WORKER_CLI_MUSE
    binary = WORKER_CLI_MUSE

    def resolve_model(self, graph_model: str | None) -> str:
        row = _graph_model_row(graph_model)
        return {
            MODEL_CHEAP: MUSE_EFFORT_CHEAP,
            GRAPH_MODEL_SONNET: MUSE_EFFORT_SONNET,
            GRAPH_MODEL_OPUS: MUSE_EFFORT_OPUS,
        }[row]

    def argv(self, resolved_binary: str, mapped_model: str, prompt: str) -> list[str]:
        # `exec` reads the prompt from its positional arg (never stdin) and emits JSONL
        # events on stdout, not one JSON object. Headless workers must never block on
        # approval or interactive input, and must write files plus run shell commands, so
        # approval is off, the sandbox is off, and user-input prompts auto-resolve.
        # `--trust-workspace` gives the worker this workspace's skills/rules, the parity
        # with what a `claude -p` worker loads by default. The prompt stays last: it is
        # positional, so no flag may follow it.
        return [
            resolved_binary,
            "exec",
            "--json",
            "--approval-mode",
            "never",
            "--disable-sandbox",
            "--trust-workspace",
            "--user-input-auto-resolve",
            "--reasoning-effort",
            mapped_model,
            prompt,
        ]

    def parse_envelope(self, envelope: dict) -> dict:
        # muse JSONL carries no token/cost envelope; run() already lifted the terminal
        # text into {"result": ...} before dispatch parses it.
        return _null_usage()

    def run(
        self,
        resolved_binary: str,
        mapped_model: str,
        prompt: str,
        timeout: int | None,
        executor: WorkerExecutor,
        output_path: Path,
    ) -> subprocess.CompletedProcess:
        proc = executor(self.argv(resolved_binary, mapped_model, prompt), prompt, timeout)
        text, session_id = _muse_terminal_result(proc.stdout or "")
        if text is None and session_id is None:
            return proc
        return subprocess.CompletedProcess(
            proc.args,
            proc.returncode,
            stdout=json.dumps({"result": text, "session_id": session_id}),
            stderr=proc.stderr or "",
        )


def _join_command(argv: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def _pane_title(output_path: Path) -> str:
    """`{run_dir}/{node_id}/attempt-N/output.md` → `{run}:{node}`."""
    return f"{output_path.parent.parent.parent.name}:{output_path.parent.parent.name}"


def _remaining_timeout(deadline: float | None) -> int | None:
    if deadline is None:
        return None
    return max(0, int(deadline - time.monotonic()))


def _timeout_ms_argv(deadline: float | None) -> list[str]:
    if deadline is None:
        return []
    remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
    return ["--timeout-ms", str(remaining_ms)]


def _parse_orca_terminal_handle(stdout: str) -> str:
    """Parse `orca terminal create --json` stdout for a terminal handle.

    Known-good test fake: `{"handle": "term_test_handle"}`.
    Also accepts a live Orca envelope (`result.handle`), nested `terminal.handle`,
    and inner `id` (not the envelope request id).
    """
    try:
        payload = json.loads(stdout or "")
    except json.JSONDecodeError as exc:
        raise ValueError(f"orca terminal create did not return JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(
            f"orca terminal create JSON must be an object, got {type(payload).__name__}"
        )
    handle = _find_terminal_handle(payload)
    if handle is None:
        raise ValueError(f"orca terminal create JSON missing handle: {payload!r}")
    return handle


def _find_terminal_handle(payload: dict) -> str | None:
    bodies = [payload]
    result = payload.get("result")
    if isinstance(result, dict):
        bodies.append(result)
    for body in bodies:
        handle = body.get("handle")
        if isinstance(handle, str) and handle:
            return handle
        terminal = body.get("terminal")
        if isinstance(terminal, dict):
            for key in ("handle", "id"):
                nested = terminal.get(key)
                if isinstance(nested, str) and nested:
                    return nested
    inner = bodies[-1]
    ident = inner.get("id")
    if isinstance(ident, str) and ident:
        return ident
    return None


def _orca_wait(
    executor: WorkerExecutor,
    orca_binary: str,
    handle: str,
    deadline: float | None,
) -> subprocess.CompletedProcess:
    argv = [
        orca_binary,
        "terminal",
        "wait",
        "--terminal",
        handle,
        "--for",
        "tui-idle",
        *_timeout_ms_argv(deadline),
        "--json",
    ]
    return executor(argv, "", _remaining_timeout(deadline))


def _dispatch_proc(proc: subprocess.CompletedProcess) -> subprocess.CompletedProcess:
    """Drop Orca JSON stdout so dispatch does not treat `result` as Worker chat text."""
    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout="", stderr=proc.stderr or "")


class GrokOrcaWorkerCli:
    """Worker CLI identity: inner TUI is Grok; Orca hosts the pane."""

    identity = WORKER_CLI_GROK_ORCA
    binary = WORKER_CLI_GROK

    def resolve_model(self, graph_model: str | None) -> str:
        _graph_model_row(graph_model)
        return GROK_MODEL

    def argv(self, resolved_binary: str, mapped_model: str, _prompt: str) -> list[str]:
        return [
            resolved_binary,
            "--permission-mode",
            "auto",
            "--model",
            mapped_model,
            "--effort",
            GROK_EFFORT,
        ]

    def parse_envelope(self, envelope: dict) -> dict:
        return _null_usage()

    def run(
        self,
        resolved_binary: str,
        mapped_model: str,
        prompt: str,
        timeout: int | None,
        executor: WorkerExecutor,
        output_path: Path,
    ) -> subprocess.CompletedProcess:
        orca_binary = shutil.which(ORCA_BINARY)
        if orca_binary is None:
            raise FileNotFoundError(
                "orca CLI not found on PATH (required for grok-orca Worker CLI)"
            )
        if shutil.which(WORKER_CLI_GROK) is None:
            raise FileNotFoundError(
                "grok CLI not found on PATH (required for grok-orca Worker CLI)"
            )

        grok_argv = self.argv(resolved_binary, mapped_model, prompt)
        command = _join_command(grok_argv)
        title = _pane_title(output_path)
        deadline = None if timeout is None else time.monotonic() + timeout
        handle: str | None = None
        try:
            created = executor(
                [
                    orca_binary,
                    "terminal",
                    "create",
                    "--worktree",
                    "active",
                    "--title",
                    title,
                    "--command",
                    command,
                    "--json",
                ],
                "",
                _remaining_timeout(deadline),
            )
            if created.returncode != 0:
                return _dispatch_proc(created)
            handle = _parse_orca_terminal_handle(created.stdout)

            waited = _orca_wait(executor, orca_binary, handle, deadline)
            if waited.returncode != 0:
                return _dispatch_proc(waited)

            sent = executor(
                [
                    orca_binary,
                    "terminal",
                    "send",
                    "--terminal",
                    handle,
                    "--text",
                    prompt,
                    "--enter",
                    "--json",
                ],
                "",
                _remaining_timeout(deadline),
            )
            if sent.returncode != 0:
                return _dispatch_proc(sent)

            # tui-idle can fire before Grok is marked busy; give the pane a beat.
            time.sleep(10)

            finished = _orca_wait(executor, orca_binary, handle, deadline)
            if finished.returncode != 0:
                return _dispatch_proc(finished)

            # Output.md can land after idle is reported; wait before close.
            time.sleep(10)
            return _dispatch_proc(finished)
        finally:
            if handle is not None:
                try:
                    executor(
                        [
                            orca_binary,
                            "terminal",
                            "close",
                            "--terminal",
                            handle,
                            "--json",
                        ],
                        "",
                        _remaining_timeout(deadline),
                    )
                except Exception:
                    pass


_IMPLEMENTATIONS: dict[str, WorkerCli] = {
    WORKER_CLI_CLAUDE: ClaudeWorkerCli(),
    WORKER_CLI_GROK: GrokWorkerCli(),
    WORKER_CLI_CURSOR: CursorWorkerCli(),
    WORKER_CLI_GROK_ORCA: GrokOrcaWorkerCli(),
    WORKER_CLI_MUSE: MuseWorkerCli(),
}


def build_usage(*, identity: str, mapped_model: str, envelope: dict) -> dict:
    """Full per-dispatch usage object written to usage.json and copied onto the node record."""
    parsed = worker_cli_for(identity).parse_envelope(envelope)
    return {
        WORKER_CLI_KEY: identity,
        "model": mapped_model,
        "cost_usd": parsed.get("cost_usd"),
        "input_tokens": parsed.get("input_tokens"),
        "output_tokens": parsed.get("output_tokens"),
        "cache_read_tokens": parsed.get("cache_read_tokens"),
        "cache_write_tokens": parsed.get("cache_write_tokens"),
    }
