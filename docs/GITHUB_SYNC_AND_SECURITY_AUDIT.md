# GitHub Sync and Security Audit — AgentForge

**Date:** 2026-11-10
**Repository:** https://github.com/Xzh1844963039/AgentForge (visibility: **PUBLIC**, default branch `main`)
**Local root:** `<local AgentForge project root>`
**Operator flow:** audit → offline regression → commit report → fast-forward push → post-push verification.

## 1. Commit SHAs (before sync)

| Ref | SHA |
|---|---|
| `origin/main` (before push) | `606c6e554703f8c588c3817210784cb8b690586f` |
| local `main` (before report commit) | `cf9a2f515ee94e3d8eea0f2b976d4ceb9ce97a34` |
| local `main` (after report commit, pushed) | see §8 |

- `git fetch origin` executed before the comparison; fetch head matched `origin/main`.
- **Ahead: 13 commits. Behind: 0 commits.** Remote is an ancestor of local
  `main`, so the sync is a plain fast-forward (no force, no rebase, no reset).
- Working tree was clean before the audit (`git status --short` empty; `git diff`
  empty), so no user modification was pending.

## 2. Commits uploaded (13 feature commits + this report)

| # | SHA | Date | Subject |
|---:|---|---|---|
| 1 | `d5b3ff8` | 2026-11-09 16:04 | fix(live): allow reasoning tokens in HKGAI connectivity stage |
| 2 | `2cb47b4` | 2026-11-09 17:20 | feat(llm): add optional HKGAI SSE streaming and diagnostic probe |
| 3 | `a07e7c1` | 2026-11-09 19:27 | test(hkgai): record authorized streaming probe result |
| 4 | `bfadc10` | 2026-11-09 19:45 | test(hkgai): prepare two minimal Stage 2 control experiments |
| 5 | `7205915` | 2026-11-09 20:05 | test(hkgai): record A/B Stage 2 control experiment results |
| 6 | `ce95ae4` | 2026-11-09 20:48 | feat(planner): add compact HKGAI protocol and record Stage 2 probe |
| 7 | `c649b49` | 2026-11-10 00:38 | feat(runtime): wire compact protocols for HKGAI and add bounded Stage 2 runner |
| 8 | `5b9870e` | 2026-11-10 00:45 | fix(llm): avoid oversized repair prompts after truncated HKGAI replies |
| 9 | `2c6ff05` | 2026-11-10 00:56 | feat(agents): minimize HKGAI action prompts with bounded tool ledger |
| 10 | `bb41624` | 2026-11-10 00:59 | fix(stage2): add inter-request cooldown for HKGAI gateway |
| 11 | `cc8c878` | 2026-11-10 01:13 | docs(stage2): record bounded real Stage 2 runtime results and gateway finding |
| 12 | `774b8fe` | 2026-11-10 12:48 | feat(transport): add fresh-connection probe, error taxonomy and trust_env lever |
| 13 | `cf9a2f5` | 2026-11-10 13:04 | docs(transport): record fresh-connection experiment (case C) and registry proxy finding |
| 14 | this report | 2026-11-10 | docs(git): add sync and security audit report |

Diff scope of the range: **75 files changed (55 added, 20 modified)**,
+7,872 / −93 lines — source (`src/agentforge/...`), tests, docs and benchmark
evidence.

## 3. Security scan results (BEFORE push)

Method: Gitleaks was **not installed**, so a local in-memory scanner was used
(kept outside the repository; the real key value never appeared on any command
line or in any log). It read blob contents through git plumbing across
**all 499 unique blobs reachable from all 35 commits** (i.e. the whole history,
not only the 13-commit range) and additionally matched the exact values of the
provider keys configured in the local `.env`.

| Check | Result |
|---|---|
| Exact real HKGAI API key value anywhere in history | **0 matches** |
| DeepSeek API key (configured? none in `.env`; nothing to match) | n/a — no value configured |
| `Authorization` / `Bearer` values | 0 |
| GitHub tokens (`ghp_`, `github_pat_`) | 0 |
| OpenAI-style real keys (`sk-` + 16 chars) | 0 |
| Private key blocks (`-----BEGIN … PRIVATE KEY-----`) | 0 |
| Assigned secret-looking values | 12 hits, **all placeholders**: empty `AGENTFORGE_*_API_KEY=` lines in `.env.example` (value length 0) and fake test fixtures (`sk-test`, `sk-secret`, `sk-secret-value`, `test-key-not-real`, `dummy-not-real`) |
| `.env` tracked? | **No.** Never tracked in any commit; no file named `.env` exists anywhere in history; ignored by `.gitignore` line 16 |
| Databases / SQLite / caches / runtime data tracked | No `*.db`, `*.sqlite`, `__pycache__`, `.pytest_cache`; only `data/.gitkeep` plus intentional benchmark task/run `.jsonl` datasets |
| Personal data in the 75 changed files | 0 Windows user paths, 0 phone numbers, 0 private LAN hosts |
| Private-host strings found | `192.168.1.1`, `metadata.google.internal` — SSRF **blocklist fixtures** in `src/agentforge/tools/search.py` and `tests/test_search_tools.py`, not infrastructure |
| Emails | 2 hits, both fake test fixtures (`tests/test_git_tools.py`, `tests/test_search_tools.py`) |
| Headers/keys in real-request evidence JSON | None: probe/stage2 JSONs store only whitelisted response headers, token counts, timings and previews; the transport probe records only that it sent `Connection: close` |

**Sensitive information found: NO.** The push was not blocked.

Informational (intentionally public, non-secret, listed for transparency):

- Real HKGAI request records and prompt previews are included as audit evidence:
  `benchmarks/runs/hkgai_probe_20261109_192627/`,
  `benchmarks/runs/hkgai_experiments_20261109_200003/`,
  `benchmarks/runs/hkgai_compact_planner_20261109_202704/`,
  `benchmarks/runs/hkgai_stage2_20261110_*/` (6 runs),
  `benchmarks/runs/hkgai_transport_20261110_124839/`,
  `benchmarks/runs/live_stages_20261110_010621/`,
  `benchmarks/runs/live_stages_real/…`.
  They contain model names, token counts, timings, our own prompt previews and
  short model-response excerpts — **no credentials, no third-party personal
  data**.
- `docs/HKGAI_PLANNER_REQUEST_SAMPLE.json` contains the model alias, the
  project's own planner system prompt and sampling parameters; no auth header.
- Documented infrastructure strings: course endpoint `api1.hkgai.net`, local
  registry proxy `127.0.0.1:7897` (diagnosis only), model aliases
  `t2_hkgai-v3_fp8_1m_e7` / `t2_hkgai-v3_fp8_1m_e10_meeting`.

## 4. `.gitignore` review

Ignores: `__pycache__`, `*.py[cod]`, build/dist artefacts, `.venv/`, **`.env` and
`.env.*` (with `!.env.example`)**, `data/*`, benchmark workspaces/DBs/logs,
`.pytest_cache/`, `.coverage`, caches and editor files. Sufficient for this repo:
no tracked file matched an ignored-pattern violation.

## 5. Code completeness (15 feature areas)

All verified present in the local `main` tree:

1. Multi-Agent Runtime + Coordinator/Research/Coding/Reviewer — PASS
2. Dynamic DAG planning + capability Dispatcher — PASS
3. ToolExecutor + permission policy + schema validation — PASS
4. Docker sandbox + Python tools — PASS
5. Memory evolution (EPISODIC / SEMANTIC / PROCEDURAL / WORKING) — PASS
6. HITL approval, clarification, pause/resume/cancel — PASS
7. Benchmark, ablation, fault injection/reliability — PASS
8. HKGAI and DeepSeek providers — PASS
9. Compact Planner (`COMPACT_PLANNER_PROTOCOL`, auto-selection) — PASS
10. Compact Agent Action Prompt (compact action protocol, one-line tool catalog,
    bounded ledger, 214-char policy prompt) — PASS
11. Structured output repair (bounded, truncation-safe) — PASS
12. HKGAI streaming (SSE) support — PASS
13. HTTP transport audit + stable error classification — PASS
14. `AGENTFORGE_HTTP_TRUST_ENV` proxy control — PASS
15. Stage 2 real-run report, transport probe and diagnosis docs — PASS

README/docs honesty check: Stage 2 is still labelled
**PARTIALLY_VERIFIED** (A/B/C live; D–G blocked by the HKGAI gateway no-response
failures). No real HKGAI test is recorded as PASS that did not pass.

## 6. Offline regression (no live API calls)

| Check | Result |
|---|---|
| `pytest -q` | **314 passed, 2 skipped** |
| `pytest -m docker -q` | **5 passed** (Docker 29.9.2) |
| `ruff check src tests --select F,E9` | **All checks passed** |
| `git diff` / working tree | clean; no unrelated architecture change |

No HKGAI/DeepSeek paid request was made during this audit; `--allow-live-api`,
Stage 3–6 and all live-stage commands were not used.

## 7. Push

Executed **after** this report was committed:

```text
git push origin main        # plain fast-forward; no --force, no rebase, no reset
```

Conditions verified before pushing: correct remote, remote is ancestor,
`ahead=13`, `behind=0`, working tree clean, no sensitive information found.

## 8. Post-push verification

_Completed after the push; see the updated values below._

| Item | Value |
|---|---|
| local `main` SHA after push | _pending_ |
| `origin/main` SHA after fetch | _pending_ |
| ahead / behind | _pending_ |
| GitHub commit matches local | _pending_ |
| README readable on GitHub | _pending_ |
| `.env` / API key present in pushed tree | _pending_ |

## 9. Remaining issues / notes

- **HKGAI Stage 2 D–G remain unverified**: the gateway returns no response for
  the follow-up request (transport experiment case C). Stage 2 stays
  PARTIALLY_VERIFIED.
- One authorized live request remains unused (11/12 consumed, 4,057/20,000
  tokens) and was **not** spent during this sync task.
- The public evidence JSONs include real model excerpts; they contain no secrets
  (reviewed in §3). Remove them from a future commit before the final submission
  only if the course requires it — this is a content decision, not a security
  blocker.
- Docker Desktop had to be restarted locally before the Docker suite; CI is not
  configured, so Docker tests remain a local-only gate.
