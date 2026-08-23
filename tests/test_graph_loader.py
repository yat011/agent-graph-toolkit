"""Tests for agentgraph_engine.graph_loader — importlib-based dynamic loading of a graph.py."""

from pathlib import Path

import pytest

from agentgraph_engine.graph_loader import (
    GraphLoadError,
    get_build_graph,
    load_graph_module,
    resolve_graph_path,
    resolve_template_path,
)

BUILD_GRAPH_MODULE = '''
from langgraph.graph import StateGraph, START, END
from typing import TypedDict

class S(TypedDict, total=False):
    x: int

def _inc(state):
    return {"x": state.get("x", 0) + 1}

def build_graph(checkpointer=None):
    g = StateGraph(S)
    g.add_node("inc", _inc)
    g.add_edge(START, "inc")
    g.add_edge("inc", END)
    return g.compile(checkpointer=checkpointer)
'''

BARE_GRAPH_MODULE = '''
from langgraph.graph import StateGraph, START, END
from typing import TypedDict

class S(TypedDict, total=False):
    x: int

def _inc(state):
    return {"x": state.get("x", 0) + 1}

_g = StateGraph(S)
_g.add_node("inc", _inc)
_g.add_edge(START, "inc")
_g.add_edge("inc", END)
graph = _g.compile()
'''


def test_load_graph_module_with_build_graph_factory(tmp_path):
    path = tmp_path / "demo" / "graph.py"
    path.parent.mkdir(parents=True)
    path.write_text(BUILD_GRAPH_MODULE, encoding="utf-8")

    module = load_graph_module(path)
    build_graph = get_build_graph(module)
    compiled = build_graph()
    result = compiled.invoke({"x": 0})
    assert result["x"] == 1


def test_load_graph_module_with_bare_graph_variable_fallback(tmp_path):
    path = tmp_path / "demo2" / "graph.py"
    path.parent.mkdir(parents=True)
    path.write_text(BARE_GRAPH_MODULE, encoding="utf-8")

    module = load_graph_module(path)
    build_graph = get_build_graph(module)
    compiled = build_graph()
    result = compiled.invoke({"x": 5})
    assert result["x"] == 6


def test_load_graph_module_missing_file_raises(tmp_path):
    with pytest.raises(GraphLoadError):
        load_graph_module(tmp_path / "nope" / "graph.py")


def test_get_build_graph_raises_when_neither_present(tmp_path):
    path = tmp_path / "empty" / "graph.py"
    path.parent.mkdir(parents=True)
    path.write_text("x = 1\n", encoding="utf-8")
    module = load_graph_module(path)
    with pytest.raises(GraphLoadError):
        get_build_graph(module)


def test_resolve_template_path_finds_real_repo_templates():
    templates_root = Path(__file__).resolve().parent.parent / "skills" / "agentgraph-run-graph" / "templates"
    if not (templates_root / "standard-task" / "graph.py").exists():
        pytest.skip("standard-task/graph.py missing")
    path = resolve_template_path("standard-task", templates_root)
    assert path.name == "graph.py"


def test_resolve_template_path_missing_graph_raises(tmp_path):
    with pytest.raises(GraphLoadError):
        resolve_template_path("nonexistent-graph", tmp_path)


def test_resolve_graph_path_prefers_project_graph_over_template(tmp_path):
    project_root = tmp_path / "agent_works" / "graphs"
    user_root = tmp_path / "user_graphs"
    templates_root = tmp_path / "templates"
    (project_root / "demo").mkdir(parents=True)
    (project_root / "demo" / "graph.py").write_text("graph = 1\n", encoding="utf-8")
    (templates_root / "demo").mkdir(parents=True)
    (templates_root / "demo" / "graph.py").write_text("graph = 2\n", encoding="utf-8")

    path = resolve_graph_path(
        "demo",
        project_graphs_root=project_root,
        user_graphs_root=user_root,
        templates_root=templates_root,
    )
    assert path == project_root / "demo" / "graph.py"


def test_resolve_graph_path_falls_back_to_template_when_no_project_graph(tmp_path):
    project_root = tmp_path / "agent_works" / "graphs"
    user_root = tmp_path / "user_graphs"
    templates_root = tmp_path / "templates"
    (templates_root / "only-template").mkdir(parents=True)
    (templates_root / "only-template" / "graph.py").write_text("graph = 1\n", encoding="utf-8")

    path = resolve_graph_path(
        "only-template",
        project_graphs_root=project_root,
        user_graphs_root=user_root,
        templates_root=templates_root,
    )
    assert path == templates_root / "only-template" / "graph.py"


def test_resolve_graph_path_prefers_project_over_user_over_template(tmp_path):
    project_root = tmp_path / "agent_works" / "graphs"
    user_root = tmp_path / "user_graphs"
    templates_root = tmp_path / "templates"
    (project_root / "demo").mkdir(parents=True)
    (project_root / "demo" / "graph.py").write_text("graph = project\n", encoding="utf-8")
    (user_root / "demo").mkdir(parents=True)
    (user_root / "demo" / "graph.py").write_text("graph = user\n", encoding="utf-8")
    (templates_root / "demo").mkdir(parents=True)
    (templates_root / "demo" / "graph.py").write_text("graph = template\n", encoding="utf-8")

    path = resolve_graph_path(
        "demo",
        project_graphs_root=project_root,
        user_graphs_root=user_root,
        templates_root=templates_root,
    )
    assert path == project_root / "demo" / "graph.py"


def test_resolve_graph_path_prefers_user_over_template_when_no_project_graph(tmp_path):
    project_root = tmp_path / "agent_works" / "graphs"
    user_root = tmp_path / "user_graphs"
    templates_root = tmp_path / "templates"
    (user_root / "demo").mkdir(parents=True)
    (user_root / "demo" / "graph.py").write_text("graph = user\n", encoding="utf-8")
    (templates_root / "demo").mkdir(parents=True)
    (templates_root / "demo" / "graph.py").write_text("graph = template\n", encoding="utf-8")

    path = resolve_graph_path(
        "demo",
        project_graphs_root=project_root,
        user_graphs_root=user_root,
        templates_root=templates_root,
    )
    assert path == user_root / "demo" / "graph.py"


def test_resolve_graph_path_raises_when_neither_exists(tmp_path):
    with pytest.raises(GraphLoadError):
        resolve_graph_path(
            "nope",
            project_graphs_root=tmp_path / "a",
            user_graphs_root=tmp_path / "u",
            templates_root=tmp_path / "b",
        )
