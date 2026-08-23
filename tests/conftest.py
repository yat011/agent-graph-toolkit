"""Isolate Worker CLI process state so tests never read ~/.agents/agentgraph.json."""

from __future__ import annotations

import pytest

from agentgraph_engine.worker_cli import reset_worker_cli


@pytest.fixture(autouse=True)
def isolate_worker_cli(monkeypatch, tmp_path):
    reset_worker_cli()
    settings = tmp_path / "isolated-agentgraph-settings" / "agentgraph.json"
    monkeypatch.setattr(
        "agentgraph_engine.worker_cli.default_settings_path",
        lambda: settings,
    )
    yield
    reset_worker_cli()
