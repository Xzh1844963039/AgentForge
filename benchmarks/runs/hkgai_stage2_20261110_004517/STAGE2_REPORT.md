# HKGAI Real Stage 2 Report

- Mode: **LIVE**
- Status: **FAILED**
- Configured model: `t2_hkgai-v3_fp8_1m_e7`
- Workflow: `wf_db2e492006b240f5bcddee4fffe8f230` (failed)
- Goal: Create and sandbox-execute a Python script named stage2_sum.py that prints exactly "STAGE2_SUM=5" by computing 2 + 3, then have the final report quote the recorded stdout and exit code from the tool audit.
- Plan subtask cap: 2; review phase: False
- Budget: 10 requests / 18600 tokens (per-request cap 1024)
- Requests: 1
- Tokens: 0
- Elapsed: 64.062s
- Hard-stopped: True (LLMProviderError after 63.969s: hkgai transport error after 63971 ms: RemoteProtocolError: Server disconnected without sending a response.)

## Checks A–G

| Check | Passed | Detail |
|---|---|---|
| `A_real_hkgai_compact_prompts` | True | provider=hkgai, provider_class_ok=True, models=[], planner=compact, agent=compact, plan_compact_prompt=True, action_calls=0 |
| `B_plan_validated` | False | plan_subtasks=0, plan_attempts=0, plan_errors=0 |
| `C_capability_dispatch` | False | assigned=[] |
| `D_agent_structured_action` | False | executing_agent_calls=0, completed_plan_tasks=0 |
| `E_action_schema_permission_validation` | False | successful_tool_calls=0/0 |
| `F_sandbox_tool_execution` | False | no successful python_execute record |
| `G_tool_evidence_in_final_report` | False | evidence_in_prompt=False, marker_in_final_report=False, final_chars=0 |

## Model calls

| # | Purpose | Model | HTTP | Tokens | s |
|---:|---|---|---:|---:|---:|
| 1 | `plan` | `n/a` | None | 0 | 63.969 |
