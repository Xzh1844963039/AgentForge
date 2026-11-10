# HKGAI Consecutive Request Failure — HTTP Transport Diagnosis

**Date:** 2026-11-10
**Status:** Case C — the first request of the fresh-connection experiment already
failed without a response; online requests were stopped.
**Budget:** 11 of 12 authorized requests, 4,057 of 20,000 returned tokens; 1
request left unused.

This document does **not** change the Stage 2 verdict: Stage 2 remains
PARTIALLY_VERIFIED (A/B/C live, D–G blocked).

## 1. HTTP transport audit (Part 1)

| # | Question | Finding |
|---:|---|---|
| 1 | Is one `httpx.AsyncClient` reused across requests? | **Yes.** `OpenAICompatibleProvider._get_client()` lazily creates one client per provider instance and reuses it. `aclose()` closes it and a later call recreates it. Verified by `test_provider_reuses_one_client_and_aclose_recreates`. |
| 2 | Are HTTP/1.1 keep-alive TCP connections reused? | **Yes by default.** With a real local HTTP/1.1 server, two sequential `complete()` calls used the *same* client-side TCP port (same pooled connection). Verified by `test_default_keep_alive_reuses_the_same_tcp_connection`. |
| 3 | Stale-connection / pool risk? | Possible in principle (server closes an idle keep-alive socket). The experiment below removed this variable by using a brand-new client and `Connection: close` for every request. |
| 4 | Do multiple agents share one provider? | **Yes, by design.** `AgentForgeRuntime` builds one provider instance and passes it to every agent. All LLM traffic goes through one client/pool in a runtime process. |
| 5 | Concurrency / lock contention / pool exhaustion? | **None inside a workflow.** `TaskOrchestrator._run_graph()` awaits each task (`await self._execute_task(...)`), so LLM calls are strictly sequential. `httpx.AsyncClient` is concurrency-safe anyway. No semaphore or lock is held across calls. |
| 6 | Unconsumed bodies / unclosed responses? | **No.** The non-streaming path uses `client.post()` (body fully read, connection returned to the pool) and `_parse_response()` calls `response.json()`. The streaming path uses `async with client.stream(...)`. No leaked response objects. |
| 7 | Anomalous message history on the second request? | **No.** In compact mode every turn is rebuilt from the base messages plus a bounded ledger (`[TOOL_RESULTS]`), measured at 1,229 → 1,375 chars over three tool turns. Repairs never echo truncated replies. |
| 8 | Cooldown / timeout / client-release errors? | No functional error found; the Stage 2 runner's 60s cooldown did not prevent the failures, and the probe here uses no cooldown. Client release is correct (`client.is_closed == True`, pool connections 0 after `Connection: close`). |

Workflow session vs HKGAI session: AgentForge has no server-side session concept;
each HTTP request is independent. There is no cookie/session header, no
`conversation_id` sent to the provider, and no server-side session key. Any
"session" behavior is entirely on the gateway/proxy side.

### 1.1 New transport finding: a system registry proxy is in the request path

- `HTTP_PROXY`/`HTTPS_PROXY` are **not** set.
- `urllib.request.getproxies()` nevertheless returns
  `{'http': 'http://127.0.0.1:7897', 'https': 'http://127.0.0.1:7897'}` — the
  Windows **registry** proxy (a local Clash-style proxy).
- httpx defaults to `trust_env=True`, and `client._get_proxy_map(None, True)`
  returns that proxy; the provider's mounted transports are `AsyncHTTPProxy`
  with `_proxy_url = http://127.0.0.1:7897`.
- **Consequence:** every HKGAI request from AgentForge goes through the local
  proxy at `127.0.0.1:7897`, including the successful ones. Earlier "no proxy"
  checks only looked at environment variables.

A new explicit lever was added to make this controllable without changing
default routing: `AGENTFORGE_HTTP_TRUST_ENV` (default `true`). Setting
`false` bypasses the system/registry proxy for provider requests.

## 2. Fresh-connection experiment (Part 2)

Prepared offline, then executed once with the remaining budget:

- exact same HKGAI provider/model/base URL as the runtime;
- the real compact Agent Action prompt rebuilt through the runtime message
  builder: **1,376 chars**, sha256[:16] `aae7d99fba17716a`, compact action
  protocol present, tool catalog present;
- non-streaming, `retries=0`, timeout 120s, `max_tokens=1024`;
- no tools executed, no workspace/Git side effects;
- a **brand-new `httpx.AsyncClient`** per request, explicit
  `Connection: close`, client closed afterwards;
- requests strictly sequential; second request only after a usable first;
- stop immediately on any request without a response.

### Result (Case C)

| # | client | fresh | conn hdr | headers | elapsed | HTTP | tokens | status |
|---:|---|---|---|---|---:|---:|---:|---|
| 1 | `client@1811631068352` | yes | `close` | none | 119.859s | none | 0 | failed |

- Error: `RemoteProtocolError: Server disconnected without sending a response`
  (`error_class=no_response_disconnect`), 0 tokens, no headers.
- Proxy in path for this request: `http://127.0.0.1:7897`.
- The probe stopped immediately: **request 2 was never sent**.
- Evidence: `benchmarks/runs/hkgai_transport_20261110_124839/transport_probe.json`
  and `TRANSPORT_REPORT.md` (no Authorization header or key recorded).

### What this rules out

- **TCP/HTTP connection reuse is not the root cause.** A brand-new client with
  `Connection: close` still received no response at all, so the earlier
  `RemoteProtocolError` was not a stale/kept-alive socket from the previous
  request.
- The failure mode also changed shape (previous runs: connection cut at
  ~56–64s; this run: silence for ~120s), which points to a gateway/proxy/upstream
  availability problem that varies over time rather than a deterministic client
  bug.

### Remaining hypotheses (not yet tested — no budget left)

1. **Local proxy path (`127.0.0.1:7897`)**: the proxy accepts the connection but
   never delivers an upstream response for this endpoint/shape. Test:
   `AGENTFORGE_HTTP_TRUST_ENV=false` (direct) with the same prompt.
2. **Per-key request throttling / queueing** at the gateway: earlier runs showed
   first requests returning and second requests cut, but this run's first
   request failed, so throttling alone does not explain everything.
3. **Upstream model instance scheduling / pre-response timeout** near 60s
   (previous runs) or >120s (this run).
4. **Route/model alias capacity**: `t2_hkgai-v3_fp8_1m_e7` → served model
   `t2_hkgai-v3_fp8_1m_e10_meeting`.

## 3. Offline hardening delivered with this diagnosis

- `LLMProviderError.error_class` taxonomy: `no_response_disconnect`, `timeout`,
  `transport`, `http_client_error`, `http_server_error`, `parse_error`,
  `provider_error`, wired into every provider error path (including streaming) and
  used by the Stage 2 stop-on-disconnect gate.
- `AGENTFORGE_HTTP_TRUST_ENV` (default `true`) plumbed through
  `Settings → build_llm_provider → HKGAI/DeepSeek → OpenAICompatibleProvider`.
- Two-request transport probe with per-request evidence fields (request id,
  workflow/agent labels, provider/client instance, fresh-client flag, proxy,
  request/response timestamps, header latency, TCP client address, connection
  reuse, pool state, client-closed flag, status, tokens) and strict stop rules.
- New tests: 6 transport-probe tests (including a real local HTTP server proving
  two distinct TCP connections) and 7 provider lifecycle/connection-reuse/error
  class tests, plus a bounded-context growth test for compact tool loops.

Measured context sizes for the real HKGAI path after this change set:

| Prompt | Chars |
|---|---:|
| Compact planner (plan call) | 836–913 |
| Worker agent system prompt | 1,110 |
| Worker action prompt, tool turns 1→3 | 1,229 → 1,335 → 1,374 |
| Synthesis / finalize policy system prompt | 214 |
| Policy task prompts (with evidence) | 1,122–1,342 |

Runtime call relationships: one provider instance is shared by all agents; the
DAG executor awaits tasks sequentially, so there is no concurrent LLM traffic
inside a workflow and no lock held across requests. Tool permissions, approval
gates and the Docker sandbox are unchanged; the final regression for this change
set is **314 passed, 2 skipped**, Docker **5/5**, Ruff F/E9 clean, coverage
**74%**.

## 4. Questions for the HKGAI / course provider

1. Is `api1.hkgai.net` reachable only through a specific network path, and does
   the course recommend a proxy configuration for it?
2. Is there a per-key concurrency or queue limit that explains a request waiting
   >120s with no response headers?
3. Does the gateway have a pre-response timeout (~60s in earlier runs, >120s in
   the latest)? What is the supported timeout for interactive agent loops?
4. Should structured planning/agent loops use SSE streaming or a dedicated
   long-request route?
5. Is `t2_hkgai-v3_fp8_1m_e7` the correct alias for multi-turn structured output,
   or should a different approved alias be used?

## 5. Stage 2 status after this experiment

- Live-verified: A (real provider + compact protocols), B (valid plan + DAG
  validation), C (capability dispatch).
- Not verified: D (agent action), E (action validation), F (sandbox execution),
  G (evidence in final report) — blocked by the gateway returning no response.
- Remaining budget: 1 request (insufficient for the minimum 5-call A–G
  workflow), so no further online attempt was made.
- Recommended next step (with fresh authorization):
  `AGENTFORGE_HTTP_TRUST_ENV=false` + the bounded Stage 2 runner, plus the
  provider questions above.
