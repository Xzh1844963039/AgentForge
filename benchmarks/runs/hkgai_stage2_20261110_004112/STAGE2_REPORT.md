# HKGAI Real Stage 2 Report

- Mode: **LIVE**
- Status: **FAILED**
- Configured model: `t2_hkgai-v3_fp8_1m_e7`
- Workflow: `wf_5e6387840ad74a518a4f9effdea8849e` (failed)
- Goal: Create and sandbox-execute a Python script named stage2_sum.py that prints exactly "STAGE2_SUM=5" by computing 2 + 3, then have the final report quote the recorded stdout and exit code from the tool audit.
- Plan subtask cap: 2; review phase: False
- Requests: 2
- Tokens: 1366
- Elapsed: 65.796s
- Hard-stopped: True (LLMProviderError after 55.515s: hkgai transport error after 55509 ms: RemoteProtocolError: Server disconnected without sending a response.)

## Checks A–G

| Check | Passed | Detail |
|---|---|---|
| `A_real_hkgai_compact_prompts` | True | provider=hkgai, provider_class_ok=True, models=['t2_hkgai-v3_fp8_1m_e10_meeting'], planner=compact, agent=compact, plan_compact_prompt=True, action_calls=0 |
| `B_plan_validated` | False | plan_subtasks=0, plan_attempts=0, plan_errors=0 |
| `C_capability_dispatch` | False | assigned=[] |
| `D_agent_structured_action` | False | executing_agent_calls=0, completed_plan_tasks=0 |
| `E_action_schema_permission_validation` | False | successful_tool_calls=0/0 |
| `F_sandbox_tool_execution` | False | no successful python_execute record |
| `G_tool_evidence_in_final_report` | False | evidence_in_prompt=False, marker_in_final_report=False, final_chars=0 |

## Model calls

| # | Purpose | Model | HTTP | Tokens | s |
|---:|---|---|---:|---:|---:|
| 1 | `plan` | `t2_hkgai-v3_fp8_1m_e10_meeting` | 200 | 1366 | 9.969 |
| 2 | `plan` | `n/a` | None | 0 | 55.515 |
