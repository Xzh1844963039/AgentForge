# HKGAI Real Stage 2 Report

- Mode: **LIVE**
- Status: **FAILED**
- Configured model: `t2_hkgai-v3_fp8_1m_e7`
- Workflow: `wf_f355142d43494153989e4c1538e6b0ad` (failed)
- Goal: In the sandbox, run Python that prints "STAGE2_SUM=5"; then report the recorded stdout and exit code.
- Plan subtask cap: 1; review phase: False
- Budget: 7 requests / 17700 tokens (per-request cap 1024)
- Requests: 2
- Tokens: 1172
- Elapsed: 64.062s
- Hard-stopped: True (LLMProviderError after 56.265s: hkgai transport error after 56258 ms: RemoteProtocolError: Server disconnected without sending a response.)

## Checks A–G

| Check | Passed | Detail |
|---|---|---|
| `A_real_hkgai_compact_prompts` | True | provider=hkgai, provider_class_ok=True, models=['t2_hkgai-v3_fp8_1m_e10_meeting'], planner=compact, agent=compact, plan_compact_prompt=True, worker_calls=1, policy_calls=0 |
| `B_plan_validated` | True | plan_subtasks=1, plan_attempts=1, plan_errors=0 |
| `C_capability_dispatch` | True | assigned=['coding'] |
| `D_agent_structured_action` | False | executing_agent_calls=0, completed_plan_tasks=0 |
| `E_action_schema_permission_validation` | False | successful_tool_calls=0/0 |
| `F_sandbox_tool_execution` | False | no successful python_execute record |
| `G_tool_evidence_in_final_report` | False | evidence_in_prompt=False, marker_in_final_report=False, final_chars=0 |

## Model calls

| # | Purpose | Model | HTTP | Tokens | s |
|---:|---|---|---:|---:|---:|
| 1 | `plan` | `t2_hkgai-v3_fp8_1m_e10_meeting` | 200 | 1172 | 7.735 |
| 2 | `execute:coding:coding` | `n/a` | None | 0 | 56.265 |
