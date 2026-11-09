# HKGAI Real Stage 2 Report

- Mode: **LIVE**
- Status: **FAILED**
- Configured model: `t2_hkgai-v3_fp8_1m_e7`
- Workflow: `wf_051e4bbb09194260946d49faf4ba3a7a` (failed)
- Goal: In the sandbox, run Python that prints "STAGE2_SUM=5"; then report the recorded stdout and exit code.
- Plan subtask cap: 1; review phase: False
- Budget: 5 requests / 16500 tokens (per-request cap 1024); inter-request delay 60.0s
- Requests: 1
- Tokens: 0
- Elapsed: 64.062s
- Hard-stopped: True (LLMProviderError after 63.985s: hkgai transport error after 63984 ms: RemoteProtocolError: Server disconnected without sending a response.)

## Checks A–G

| Check | Passed | Detail |
|---|---|---|
| `A_real_hkgai_compact_prompts` | False | provider=hkgai, provider_class_ok=True, models=[], planner=compact, agent=compact, plan_compact_prompt=True, worker_calls=0, policy_calls=0 |
| `B_plan_validated` | False | plan_subtasks=0, plan_attempts=0, plan_errors=0 |
| `C_capability_dispatch` | False | assigned=[] |
| `D_agent_structured_action` | False | executing_agent_calls=0, completed_plan_tasks=0 |
| `E_action_schema_permission_validation` | False | successful_tool_calls=0/0 |
| `F_sandbox_tool_execution` | False | no successful python_execute record |
| `G_tool_evidence_in_final_report` | False | evidence_in_prompt=False, marker_in_final_report=False, final_chars=0 |

## Model calls

| # | Purpose | Model | HTTP | Tokens | s |
|---:|---|---|---:|---:|---:|
| 1 | `plan` | `n/a` | None | 0 | 63.985 |
