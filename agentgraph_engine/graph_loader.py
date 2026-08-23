"""Dynamic loading of a `graph.py` by file path (deliverable d).

Both kinds of Graph this toolkit knows about load the same way, via stdlib `importlib` — never
copied into a project's `agent_works/`, and never via `langgraph-cli`/`langgraph.json`:

- **Template graphs** (CONTEXT.md): the two built-in, reusable graphs shipped inside
  `skills/agentgraph-run-graph/templates/{name}/graph.py` (`feature-kickoff`, `standard-task`).
- **Project graphs**: a graph `agentgraph-define-graph` wrote for one specific plan, at
  `agent_works/graphs/{name}/graph.py` — the same location the retired `graph.md` engine used for
  a custom, non-template graph (that folder predates, and is unrelated to, a Run's own
  `agent_works/{graph_name}/runs/{run_id}/` folder).

`resolve_graph_path` checks the project location first (a project may deliberately shadow a
built-in template name), falling back to the built-in templates.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Callable, Optional

TEMPLATES_ROOT = (
    Path(__file__).resolve().parent.parent / "skills" / "agentgraph-run-graph" / "templates"
)
DEFAULT_PROJECT_GRAPHS_ROOT = Path("agent_works") / "graphs"


class GraphLoadError(Exception):
    pass


def resolve_template_path(graph_name: str, templates_root: Path = TEMPLATES_ROOT) -> Path:
    path = templates_root / graph_name / "graph.py"
    if not path.exists():
        raise GraphLoadError(f"No template graph.py for '{graph_name}' at {path}")
    return path


def resolve_graph_path(
    graph_name: str,
    *,
    project_graphs_root: Path = DEFAULT_PROJECT_GRAPHS_ROOT,
    templates_root: Path = TEMPLATES_ROOT,
) -> Path:
    """Resolve a graph by name: a project-authored graph.py first, then a built-in template."""
    project_path = project_graphs_root / graph_name / "graph.py"
    if project_path.exists():
        return project_path
    return resolve_template_path(graph_name, templates_root)


def load_graph_module(path: Path | str) -> types.ModuleType:
    """Load a `graph.py` file as an isolated module via `importlib.util`."""
    path = Path(path)
    if not path.exists():
        raise GraphLoadError(f"graph.py not found at {path}")
    module_name = f"agentgraph_template__{path.parent.name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise GraphLoadError(f"Could not build an import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    # Register in sys.modules before exec: LangGraph's StateGraph resolves `from __future__
    # import annotations`-deferred type hints via typing.get_type_hints(), which looks the
    # module up by name in sys.modules for its globalns — an unregistered module fails that
    # resolution (e.g. NameError on a perfectly valid `Optional[str]` annotation).
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def get_build_graph(module: types.ModuleType) -> Callable[..., object]:
    """A loaded template must expose `build_graph(checkpointer=None)`; a bare `graph` module
    attribute (already compiled, no checkpointer) is accepted as a fallback for direct use in
    tests, per the "graph variable or build_graph() factory" requirement.
    """
    build_graph = getattr(module, "build_graph", None)
    if callable(build_graph):
        return build_graph
    graph = getattr(module, "graph", None)
    if graph is not None:
        return lambda checkpointer=None: graph
    raise GraphLoadError(
        f"Module {module.__name__} defines neither build_graph() nor a graph variable"
    )


def load_graph_by_name(graph_name: str, *, checkpointer=None, templates_root: Path = TEMPLATES_ROOT):
    """Convenience: resolve a template by name, load it, and compile/build it."""
    path = resolve_template_path(graph_name, templates_root)
    module = load_graph_module(path)
    build_graph = get_build_graph(module)
    return build_graph(checkpointer=checkpointer)
