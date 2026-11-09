# Final Submission Checklist

Status as of Phase 6A (2026-11-09). This is a preparation checklist, not a claim
that the final course submission is complete.

## 1. Code and architecture

- [x] Phase 1–5 architecture preserved; no project re-initialization
- [x] Multi-agent runtime, DAG, delegation, tools, Docker, memory, HITL, benchmark
- [x] DeepSeek/HKGAI-only provider path; no OpenAI/Anthropic runtime dependency
- [x] Structured action repair with bounded retries and audit
- [x] Task token, workflow LLM-call, tool-call and repeated-action guards
- [x] High-risk reconciliation state; no exactly-once claim
- [x] Live stage runner with cumulative request/token budget
- [x] Live memory learning/evaluation protocol
- [x] Benchmark verifier distinguishes PASSED/FAILED/NEEDS_REVIEW/SKIPPED
- [x] Budget violations clear goal success
- [ ] Official teacher test set adapter (blocked: dataset not received)
- [ ] Real-LLM stages 1–6 (blocked: no authorized API credentials)

## 2. Tests

- [x] Offline unit/integration suite
- [x] Docker integration suite (when Docker daemon is available)
- [x] Benchmark fixture suite
- [x] Fault-injection suite
- [x] Provider request/response/retry/error tests with MockTransport
- [x] Staged-live no-key safety test
- [x] Ruff F/E9
- [ ] Live provider acceptance (not run; NOT_VERIFIED)
- [ ] Real-user HITL evaluation (if required by course; simulated only now)

Final test counts and coverage must be taken from the last actual command output,
not copied from an earlier phase.

## 3. Documentation

- [x] README quick start and commands
- [x] ARCHITECTURE updated for Phase 6A
- [x] DEVELOPMENT_PLAN updated
- [x] BENCHMARK_DESIGN / EVALUATION_METRICS
- [x] ABLATION_STUDY / FAILURE_ANALYSIS
- [x] REAL_LLM_ACCEPTANCE
- [x] PHASE6A_REPORT
- [x] FINAL_EVALUATION_PROTOCOL
- [x] FINAL_SUBMISSION_CHECKLIST
- [ ] Official dataset results inserted after authorized run
- [ ] Final live acceptance table filled with real data

## 4. Configuration and secrets

- [x] API keys read from environment/`.env` only
- [x] `.env` ignored; `.env.example` documents variable names
- [x] Provider errors redact API key
- [x] No keys committed to Git
- [x] No ZCode/internal model used as product provider
- [ ] Course-authorized API key supplied by the user outside chat/logs
- [ ] Explicit paid-call authorization recorded for the final run

## 5. Docker and environment

- [x] Docker sandbox verified in earlier phases
- [x] Docker unavailable paths fail closed / tests skip honestly
- [ ] Docker daemon running for the final verification
- [ ] Clean-machine reproduction checked
- [x] No Windows system settings changed by this phase

## 6. Git / submission hygiene

- [x] Local Git history preserved
- [x] Worktree clean after phase commits (verify before submission)
- [x] No remote push
- [x] No GitHub remote created
- [ ] Final course-submission commit/tag after live/official run
- [ ] Sensitive-information scan over the final tree
- [ ] Final report and demo artifacts archived

## 7. Reproducible commands

```powershell
python -m pytest -q
python -m pytest -m docker -q
python -m pytest --cov=agentforge --cov-report=term -q
python -m ruff check src tests --select F,E9

python -m agentforge benchmark run --dataset benchmarks/dev --output-dir benchmarks/runs/dev_full
python -m agentforge benchmark compare --dataset benchmarks/dev --output-dir benchmarks/runs/ablation_official
python -m agentforge benchmark live --stage all --provider deepseek --allow-live-api --max-requests 120 --max-tokens 250000
```

The last command requires credentials and explicit authorization. Without them,
it makes no request and reports all stages `NOT_VERIFIED`.

## 8. Do not submit as final until

1. official dataset is adapted and run;
2. live stages are actually executed or explicitly documented as unavailable;
3. real results, failures and limitations are in the final report;
4. sensitive-information scan passes;
5. final Git commit is clean and locally verified;
6. no remote push is performed without approval.
