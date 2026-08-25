"""Worker CLI identities, once-per-process selection, and vendor argv/envelope implementations.

A Run dispatches every Worker through one vendor headless CLI — `claude`, `grok`, or
`cursor` (binary `cursor-agent`). Selection happens once per engine process
(`--cli` > `~/.agents/agentgraph.json` > `claude`) and lives in a ContextVar for the
life of that process. `dispatch_worker` reads `current_worker_cli()`; it does not
re-run the cascade, and the checkpointed value is not an input to the next process.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from pathlib import Path
from typing import Protocol

from agentgraph_engine.constants import (
    MODEL_CHEAP,
    WORKER_CLI_CLAUDE,
    WORKER_CLI_CURSOR,
    WORKER_CLI_CURSOR_BINARY,
    WORKER_CLI_GROK,
    WORKER_CLI_IDENTITIES,
    WORKER_CLI_KEY,
)

GRAPH_MODEL_HAIKU = "haiku"
GRAPH_MODEL_SONNET = "sonnet"
GRAPH_MODEL_OPUS = "opus"

CLAUDE_MODEL_CHEAP = "haiku"
CLAUDE_MODEL_SONNET = "sonnet"
CLAUDE_MODEL_OPUS = "opus"
GROK_MODEL = "grok-4.6"
CURSOR_MODEL = "cursor-grok-4.6-high"

_resolved_worker_cli: ContextVar[str | None] = ContextVar("agentgraph_worker_cli", default=None)


class WorkerCliError(ValueError):
    """Unknown Worker CLI identity or unreadable settings file."""


class UnknownGraphModelError(ValueError):
    """Graph `model` is not cheap/haiku/sonnet/opus/unset."""


class WorkerCli(Protocol):
    identity: str
    binary: str

    def resolve_model(self, graph_model: str | None) -> str: ...

    def argv(self, resolved_binary: str, mapped_model: str, prompt: str) -> list[str]: ...

    def parse_envelope(self, envelope: dict) -> dict: ...


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
            "high",
        ]

    def parse_envelope(self, envelope: dict) -> dict:
        return _claude_shaped_usage(envelope)


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


_IMPLEMENTATIONS: dict[str, WorkerCli] = {
    WORKER_CLI_CLAUDE: ClaudeWorkerCli(),
    WORKER_CLI_GROK: GrokWorkerCli(),
    WORKER_CLI_CURSOR: CursorWorkerCli(),
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
