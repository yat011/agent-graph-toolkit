"""Dynamic loading of a `graph.py` by file path.

Both kinds of Graph this toolkit knows about load the same way, via stdlib `importlib` — never
copied into a project's `agent_works/`, and never via `langgraph-cli`/`langgraph.json`:

- **Template graphs**: the two built-in, reusable graphs shipped inside
  `skills/agentgraph-run-graph/templates/{name}/graph.py` (`feature-kickoff`, `standard-phase`, `standard-task`).
- **User graphs**: `Path.home() / ".agents" / "graphs" / {name} / "graph.py"`.
- **Project graphs**: a graph `agentgraph-define-graph` wrote for one specific plan, at
  `agent_works/graphs/{name}/graph.py`.

`resolve_graph_path` checks project, then user, then built-in templates. A project graph may
deliberately shadow a user graph or a built-in template name.

Each loaded `graph.py` is registered as `{package}.graph` with its parent folder as a package so
sibling modules (`nodes.py`) can use relative imports.
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
DEFAULT_USER_GRAPHS_ROOT = Path.home() / ".agents" / "graphs"


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
    user_graphs_root: Path = DEFAULT_USER_GRAPHS_ROOT,
    templates_root: Path = TEMPLATES_ROOT,
) -> Path:
    """Resolve a graph by name: project, then user, then a built-in template."""
    project_path = project_graphs_root / graph_name / "graph.py"
    if project_path.exists():
        return project_path
    user_path = user_graphs_root / graph_name / "graph.py"
    if user_path.exists():
        return user_path
    return resolve_template_path(graph_name, templates_root)


def _package_name_for(path: Path) -> str:
    return f"agentgraph_template__{path.parent.name.replace('-', '_')}"


def load_graph_module(path: Path | str) -> types.ModuleType:
    """Load a `graph.py` file as an isolated module via `importlib.util`.

    The parent directory is registered as a package so `graph.py` can `from .nodes import ...`.
    """
    path = Path(path)
    if not path.exists():
        raise GraphLoadError(f"graph.py not found at {path}")
    parent = path.parent.resolve()
    package_name = _package_name_for(path)
    module_name = f"{package_name}.graph"

    pkg = sys.modules.get(package_name)
    if pkg is None:
        pkg = types.ModuleType(package_name)
        sys.modules[package_name] = pkg
    pkg.__path__ = [str(parent)]
    pkg.__package__ = package_name
    pkg.__file__ = str(parent / "__init__.py")

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise GraphLoadError(f"Could not build an import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = package_name
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


def load_graph_by_name(
    graph_name: str,
    *,
    checkpointer=None,
    templates_root: Path = TEMPLATES_ROOT,
    project_graphs_root: Path = DEFAULT_PROJECT_GRAPHS_ROOT,
    user_graphs_root: Path = DEFAULT_USER_GRAPHS_ROOT,
):
    """Convenience: resolve a graph by name, load it, and compile/build it."""
    path = resolve_graph_path(
        graph_name,
        project_graphs_root=project_graphs_root,
        user_graphs_root=user_graphs_root,
        templates_root=templates_root,
    )
    module = load_graph_module(path)
    build_graph = get_build_graph(module)
    return build_graph(checkpointer=checkpointer)
