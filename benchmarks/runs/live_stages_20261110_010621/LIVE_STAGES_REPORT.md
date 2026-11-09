# Staged Live-LLM Acceptance

- Provider: `hkgai`
- Model: `t2_hkgai-v3_fp8_1m_e7`
- Git commit: `bb41624e3802d3f165735056aba8a985c6196291`
- API key configured: True
- Live API authorized: False
- Overall: **NOT_VERIFIED**

| Stage | Name | Status | Detail |
|---:|---|---|---|
| 1 | Connectivity smoke test | NOT_VERIFIED | live API calls were not explicitly authorized |
| 2 | Structured plan and action | NOT_VERIFIED | live API calls were not explicitly authorized |
| 3 | Real file and Docker tool execution | NOT_VERIFIED | live API calls were not explicitly authorized |
| 4 | Planning, delegation and independent review | NOT_VERIFIED | live API calls were not explicitly authorized |
| 5 | Clarification, approval and memory reuse | NOT_VERIFIED | live API calls were not explicitly authorized |
| 6 | Full live benchmark | NOT_VERIFIED | live API calls were not explicitly authorized |

## Notes

- All stages NOT_VERIFIED: live API calls were not explicitly authorized. No paid API call was made.
- Required configuration: AGENTFORGE_LLM_PROVIDER=deepseek|hkgai, the matching API key, and for HKGAI also BASE_URL and MODEL. Do not paste keys into chat or logs.
