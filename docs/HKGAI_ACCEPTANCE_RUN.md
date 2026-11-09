# HKGAI Real API Acceptance Run — 2026-11-09

## 1. Configuration

Without exposing the key:

| Item | Value |
|---|---|
| Provider | `hkgai` |
| Configured model | `t2_hkgai-v3_fp8_1m_e7` |
| Response-reported model (Stage 1) | `t2_hkgai-v3_fp8_1m_e10_meeting` |
| Base URL | course host + `/v1` (host omitted from this document) |
| Key present | yes, loaded from ignored `.env` |
| Key committed/logged | no |
| Docker | 29.9.2, running |
| Client retries for this run | 0 (to enforce actual request budget) |

The initial base URL was host-only. `GET <host>/v1/models` returned the configured
model and confirmed the API root; the host-only chat URL returned HTTP 404
("default backend - 404"). The local `.env` base URL was corrected to include
`/v1`. This is a configuration fix, not a key change.

## 2. Stage 1 — Connectivity

First attempt with `max_tokens=32` returned HTTP 200 and 121 usage tokens but an
empty content field: the reasoning model consumed the completion budget. This is
a token-budget issue, not authentication.

Minimal fix: Stage 1 now requests `max_tokens=256` and records `finish_reason`.
After the fix:

| Metric | Result |
|---|---:|
| Status | **PASSED** |
| HTTP status | 200 |
| Response content | non-empty (`OK`) |
| Configured model | `t2_hkgai-v3_fp8_1m_e7` |
| Response model | `t2_hkgai-v3_fp8_1m_e10_meeting` |
| Prompt tokens | 89 |
| Completion tokens | 36 |
| Total tokens | 125 |
| Finish reason | `stop` |
| Stage elapsed | 5.979s |
| Stage requests | 1 |
| Stage token budget | 125 / 10,000 |

Stage 1 confirms:

- authentication works;
- the endpoint path is `/v1/chat/completions`;
- a real, non-empty response is returned;
- token usage is parsed;
- the response model field identifies the served model;
- the API key does not appear in the stage report.

Including the initial 404, model-list diagnostic, empty-content attempt and a
response-shape diagnostic, Stage 1 work used **5 actual HTTP requests** and
**376 tokens**, within the authorized maximum of 5 requests / 10,000 tokens.

## 3. Stage 2 — Structured Output

The real Coordinator planning prompt (~2,300 characters) was sent to HKGAI.
Three Stage 2 attempts and one direct prompt-shape diagnostic all ended with:

```text
RemoteProtocolError: Server disconnected without sending a response.
elapsed ~65-67s
tokens returned: 0
```

Attempts:

| Attempt | Client max_tokens | Timeout | Requests | Tokens | Result |
|---|---:|---:|---:|---:|---|
| 1 | 2048 | 60s | 1 | 0 | server disconnected ~65.4s |
| 2 | 512 | 180s | 1 | 0 | server disconnected ~67.6s |
| 3 | 256 | 90s | 1 | 0 | server disconnected ~66.8s |
| 4 (direct exact prompt) | 256 | 90s | 1 | 0 | server disconnected ~64.4s |

The client timeout is not the limiting factor: attempts 2 and 3 allowed longer
than the observed disconnect and still received no response. The gateway closed
the connection before any usage or content was returned. The Stage 2 budget was
stopped at 4 of 5 authorized requests; the remaining request was not spent because
a valid plan plus action cannot be completed with one request.

Stage 2 status: **FAILED**.

No plan or structured action was produced, so no schema validation or repair path
was exercised against the real model. No claim is made that the model can or
cannot produce a valid plan until the gateway issue is resolved.

## 4. Observed provider/model behavior

- A simple Stage 1 prompt succeeds reliably and quickly with the configured model
  alias.
- The same endpoint returns a served-model string
  `t2_hkgai-v3_fp8_1m_e10_meeting` even though the configured alias is
  `t2_hkgai-v3_fp8_1m_e7`. This routing/alias behavior should be confirmed with
  the course provider.
- Longer structured planning requests are disconnected server-side at roughly
  60–67s. This blocks Stage 2 and therefore Stages 3–6.

## 5. Security and data separation

- API key is stored only in ignored `.env`.
- A direct scan for the exact key over `benchmarks/runs/live_stages_real/` and
  `data/` found **0** occurrences.
- Stage JSON/Markdown reports contain provider name, model, status, request/token
  counts and error text, but no key.
- Real HKGAI run data is stored under `benchmarks/runs/live_stages_real/`,
  separate from Fixture benchmark results.
- No Git push was performed for this run.

## 6. Regression after the minimal fix

After changing Stage 1 `max_tokens` from 32 to 256 and adding `finish_reason` to
the stage metrics:

```text
Full suite: 269 passed, 2 skipped
Docker tests: 5 passed (included in full suite)
Ruff F/E9: clean
```

## 7. Next action

The next step is not Stage 3. Stage 2 must first pass. Ask the course provider to
confirm:

1. the supported structured-output model/route for `t2_hkgai-v3_fp8_1m_e7`;
2. whether the gateway supports long non-streaming planning requests (60s limit);
3. whether streaming or a different approved route/model is required;
4. whether the served model `...e10_meeting` is the intended target.

No model/provider switch will be made without explicit authorization. Optional SSE
streaming and a one-request probe were prepared and offline-tested. The authorized
probe was then executed once: it received no HTTP response headers or bytes and the
server disconnected after 67.437s. See `docs/HKGAI_STREAMING_DIAGNOSIS.md`. No full
Live Benchmark or Stage 3–6 run was started.
