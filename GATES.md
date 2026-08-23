# Gates: state-node-composition-refactor

Scope: Refactor agentgraph_engine + both templates to composed per-node state, user-tier graph resolution, shared constants, wiring-only graph.py, and the two spec behavior changes.

- [x] G1: constants.py exists and exports ACCEPT, LOOP_BACK, MANUAL, RESULT_ACCEPT/REJECT/MANUAL, halt reasons including unrecognized_result, and halted node id
  CHECK: .venv\Scripts\python.exe -c "from agentgraph_engine.constants import ACCEPT, LOOP_BACK, MANUAL, RESULT_ACCEPT, RESULT_REJECT, RESULT_MANUAL, HALT_UNRECOGNIZED_RESULT, HALT_RETRIES_EXHAUSTED, HALT_UNMET_DEPENDENCIES, HALT_MANUAL_REQUESTED, HALT_REJECT_ATTEMPTS_EXHAUSTED, NODE_HALTED; print('ACCEPT', ACCEPT); print('LOOP_BACK', LOOP_BACK); print('MANUAL', MANUAL); print('RESULT', RESULT_ACCEPT, RESULT_REJECT, RESULT_MANUAL); print('HALTS', HALT_UNRECOGNIZED_RESULT, HALT_RETRIES_EXHAUSTED, HALT_UNMET_DEPENDENCIES, HALT_MANUAL_REQUESTED, HALT_REJECT_ATTEMPTS_EXHAUSTED); print('NODE_HALTED', NODE_HALTED)"
  EXPECT: unrecognized_result
  EVIDENCE: HALTS unrecognized_result retries_exhausted unmet_dependencies manual_requested reject_attempts_exhausted | NODE_HALTED halted

- [x] G2: SELF_RETRY is gone from routing.py; retry_attempt_key/self_attempt_key/max_self_attempts are gone
  CHECK: .venv\Scripts\python.exe -c "from pathlib import Path; t=Path('agentgraph_engine/routing.py').read_text(encoding='utf-8'); banned=['SELF_RETRY','retry_attempt_key','self_attempt_key','max_self_attempts']; hits=[b for b in banned if b in t]; print('BANNED_HITS', hits or 'none')"
  EXPECT: BANNED_HITS none
  EVIDENCE: BANNED_HITS none

- [x] G3: resolve_graph_path prefers project over user over template (pytest)
  CHECK: .venv\Scripts\python.exe -m pytest tests/test_graph_loader.py -q --tb=no
  EXPECT: passed in
  EVIDENCE: ...........                                                              [100%] | 11 passed in 0.37s

- [x] G4: unrecognized Result: line routes to MANUAL immediately with halt_reason unrecognized_result (pytest)
  CHECK: .venv\Scripts\python.exe -m pytest tests/test_standard_task_graph.py tests/test_feature_kickoff_graph.py tests/test_routing.py -q --tb=short -k unrecognized
  EXPECT: passed
  EVIDENCE: ...                                                                      [100%] | 3 passed, 20 deselected in 0.46s

- [x] G5: nested state keys used; old flat suffixes gone from engine/templates/tests
  CHECK: .venv\Scripts\python.exe -c "from pathlib import Path; roots=[Path('agentgraph_engine'), Path('skills/agentgraph-run-graph/templates'), Path('tests')]; banned=['implement_attempt_count','review_result_line','tech_review_result_line_route','review_attempt_count','planner_attempt_count','task_outcomes']; hits=[str(p)+':'+b for r in roots for p in r.rglob('*.py') if p.is_file() for b in banned if b in p.read_text(encoding='utf-8')]; print('FLAT_HITS', hits or 'none')"
  EXPECT: FLAT_HITS none
  EVIDENCE: FLAT_HITS none

- [x] G6: full pytest suite passes
  CHECK: .venv\Scripts\python.exe -m pytest -q --tb=no
  EXPECT: passed in
  EVIDENCE: ......................................................                   [100%] | 54 passed in 2.97s

- [x] G7: hello_graph uses composed BasicNodeState-style records (nested node keys, not flat prefixed fields)
  CHECK: .venv\Scripts\python.exe -m pytest tests/test_hello_graph.py -q --tb=no
  EXPECT: passed in
  EVIDENCE: ...                                                                      [100%] | 3 passed in 0.49s

- [x] G8: no make_gate_node or generic node factory in engine or templates
  CHECK: .venv\Scripts\python.exe -c "from pathlib import Path; roots=[Path('agentgraph_engine'), Path('skills/agentgraph-run-graph/templates')]; pats=['make_gate_node','make_node(']; hits=[str(p)+':'+pat for r in roots for p in r.rglob('*.py') if p.is_file() for pat in pats if pat in p.read_text(encoding='utf-8')]; print('FACTORY_HITS', hits or 'none')"
  EXPECT: FACTORY_HITS none
  EVIDENCE: FACTORY_HITS none

- [x] G9: tech_plan_reviewer prompt uses accepted/rejected not Approve/Reject
  CHECK: .venv\Scripts\python.exe -c "from pathlib import Path; files=list(Path('skills/agentgraph-run-graph/templates/feature-kickoff').rglob('*.py')); text='\n'.join(p.read_text(encoding='utf-8') for p in files); assert 'Approve' not in text, 'Approve still present'; assert 'Verdict: Reject' not in text and 'Result: Reject' not in text; assert 'accepted' in text and 'rejected' in text; print('PHRASES accepted rejected; no Approve/Reject')"
  EXPECT: PHRASES accepted rejected; no Approve/Reject
  EVIDENCE: PHRASES accepted rejected; no Approve/Reject

- [x] G10: user_graphs_root overridable parameter exists on resolve_graph_path
  CHECK: .venv\Scripts\python.exe -c "import inspect; from agentgraph_engine.graph_loader import resolve_graph_path; print(inspect.signature(resolve_graph_path))"
  EXPECT: user_graphs_root
  EVIDENCE: inspect.signature(resolve_graph_path) includes keyword-only user_graphs_root (with project_graphs_root and templates_root). Default is Path.home()/".agents"/"graphs".

- [x] G11: states package exports BasicNodeState, GateNodeState, BaseGraphState, StandardTaskState, FeatureKickoffState with map_task_states
  CHECK: .venv\Scripts\python.exe -c "from agentgraph_engine.states.base import BasicNodeState, GateNodeState, BaseGraphState; from agentgraph_engine.states.standard_task import StandardTaskState; from agentgraph_engine.states.feature_kickoff import FeatureKickoffState; print('STATES', list(StandardTaskState.__annotations__), list(FeatureKickoffState.__annotations__))"
  EXPECT: map_task_states
  EVIDENCE: STATES ['run_dir', 'halted', 'halt_reason', 'halted_at_node', 'outcome', 'item', 'implement_requirements', 'review'] ['run_dir', 'halted', 'halt_reason', 'halted_at_node', 'outcome', 'spec_path', 'cre

- [x] G12: nodes/common.py exports halted terminal; template graph.py files contain no dispatch_with_retry
  CHECK: .venv\Scripts\python.exe -c "from pathlib import Path; from agentgraph_engine.nodes.common import halted; assert callable(halted); gs=[Path('skills/agentgraph-run-graph/templates/standard-task/graph.py'), Path('skills/agentgraph-run-graph/templates/feature-kickoff/graph.py')]; assert all('dispatch_with_retry' not in p.read_text(encoding='utf-8') and 'def build_graph' in p.read_text(encoding='utf-8') for p in gs); print('HALTED_NODE_OK')"
  EXPECT: HALTED_NODE_OK
  EVIDENCE: HALTED_NODE_OK

- [x] G13: graph.py files are wiring-only (no node bodies or prompt-builders)
  EVIDENCE: Measured 2026-08-23: standard-task/graph.py 90 lines, defs only route_after_implement/route_after_review/build_graph; feature-kickoff/graph.py 157 lines, defs only route_after_* + build_graph; hello_graph/graph.py 67 lines, defs only route_after_worker/route_after_checker/build_graph. None contain dispatch_with_retry or task_prompt. Node bodies live in sibling nodes.py.

- [x] G14: define-graph SKILL.md documents project > user > template and explicit-only user-tier write
  EVIDENCE: skills/agentgraph-define-graph/SKILL.md resolves project (`agent_works/graphs/{name}/graph.py`) > user (`Path.home()/.agents/graphs/{name}/graph.py`) > template. Step 2: default write is project tier; user-tier write only when the user explicitly asks ("save this as a user graph").

- [x] G15: ENGINE-CLI.md and run-graph SKILL.md state immediate-manual for unrecognized Result, no self-retry
  EVIDENCE: ENGINE-CLI.md Halting: "`unrecognized_result` — ... Routes to manual immediately; no self-retry hop." run-graph SKILL.md: "An unrecognized `Result:` line on a gate routes to manual **immediately** — there is no self-retry hop." No remaining description of self-retry-on-unrecognized as current behavior.

- [x] G16: README.md What's here tree includes constants.py, states/, nodes/
  EVIDENCE: README.md What's here lists `constants.py`, `states/`, and `nodes/` under agentgraph_engine/.

- [x] G17: redrive zeros nested attempt_count on gate-manual halts
  EVIDENCE: cli.py `_reset_nested_attempt_records` copies every top-level dict with `attempt_count` and sets it to 0 when halt_reason is in GATE_HALT_REASONS. tests/test_redrive.py::test_redrive_resets_attempt_count_after_manual_resolution asserts implement_requirements/review attempt_count go 3 -> 1 after redrive. Route is not cleared on the forked checkpoint because LangGraph update_state reapplies the last node's conditional edges.

- [x] G18: map_task_states replaces task_outcomes (final_review derives from map_task_states)
  EVIDENCE: feature-kickoff/nodes.py run_tasks returns `{"map_task_states": map_task_states}` (full child invoke state, or `{item, outcome: blocked}`). `_final_review_prompt` reads `state.get("map_task_states")`. No `task_outcomes` remains in engine/templates/tests *.py.

- [x] G19: nothing silently dropped from current FeatureKickoffState/StandardTaskState fields
  EVIDENCE: StandardTask: implement_attempt_count/result_line -> implement_requirements.attempt_count/result_line; review_attempt_count/result_line/route/halt_reason -> review.*; review_self_retry_count dropped (SELF_RETRY removed). FeatureKickoff: branch_result_line -> create_feature_branch.result_line; planner_attempt_count/plan_output_path -> planner.attempt_count/output_path; tech_review_* -> tech_plan_reviewer.*; load_tasks_result_line/items -> load_tasks.result_line/items (LoadTasksNodeState); task_outcomes -> map_task_states; final_review_* -> final_review.*; *_self_retry_count dropped. Graph-level run_dir/halted/halt_reason/halted_at_node/outcome/spec_path kept.

- [x] G20: routing.py docstring is current facts only; classify_gate/gate_route read nested self_node records
  EVIDENCE: routing.py module docstring states current classification (unrecognized -> MANUAL immediately, halt_reason unrecognized_result; budget reads state[retry_target]["attempt_count"]). classify_gate uses `_record(state, config.self_node)` for result_line; gate_route reads `_record(state, config.self_node).get("route")`. No SELF_RETRY, no history/reasoning narrative.
