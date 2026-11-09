# HKGAI Stage 2 Connection Diagnosis and Streaming Support

**Date:** 2026-11-09
**Scope:** offline diagnosis only. No real API request was made while preparing
this document; the controlled streaming probe awaits explicit authorization.

## 1. Request construction audit

Stage 1 and Stage 2 use the same `OpenAICompatibleProvider` and endpoint. The
only differences are the messages and completion budget:

| Field | Stage 1 | Stage 2 planning |
|---|---|---|
| Endpoint | `<base>/v1/chat/completions` | same |
| `model` | configured HKGAI model | same |
| Messages | one short user message | system `PLANNER_PROTOCOL` + ~2,300-character user plan prompt |
| `temperature` | 0.2 | 0.2 |
| `max_tokens` | 256 (after fix) | tried 2048, 512, 256 |
| `stream` | false | false |
| Authorization header | Bearer key | Bearer key |
| Retries in diagnostic stages | 0 | 0 |
| Tools / side effects | none | none |

No unsupported parameter is present. `stream_options` is not sent. The Stage 2
payload is a standard Chat Completions request. Stage 1 demonstrates that the
endpoint, auth, model, usage parsing and response parsing work.

## 2. Planning prompt size

- `PLANNER_PROTOCOL`: 1,213 characters.
- Agent catalog: 952 characters.
- Goal + framing: ~135 characters.
- Full user + system prompt: ~2,300 characters (roughly 600 tokens).

This is not an unusually large prompt. The failure is therefore unlikely to be a
simple prompt-size limit.

## 3. Client/network settings

- `httpx` 0.28.1.
- Default HTTP/1.1; HTTP/2 is not enabled.
- Default connection pool (100 max, 20 keep-alive).
- No `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` / `NO_PROXY` variables set.
- Client trust_env is default true, but no proxy environment is present.
- Stage 2 attempts used client timeouts of 60s, 180s and 90s.
- Server closed at ~65.4s, ~67.6s and ~66.8s even when the client timeout was
  180s.
- Provider retries were disabled (`max_retries=0`) for those attempts; each
  attempt made one request and returned no usage/content.

Conclusion: the disconnect is server/gateway-side, not a client timeout or a
retry loop.

## 4. Reasoning-model evidence

A direct non-streaming diagnostic with the same server/model returned:

- `finish_reason=stop`
- `content="OK"`
- `reasoning` present (173 chars in the diagnostic)
- `completion_tokens_details.reasoning_tokens=38`
- total usage 130 tokens

Stage 1 with `max_tokens=32` returned empty content and 32 completion tokens;
raising the completion budget to 256 produced non-empty content. This confirms
the served model emits reasoning tokens before content. It is plausible that the
structured planning prompt triggers much longer reasoning, exceeding the
server/gateway's ~60s response window.

## 5. Model alias observation

- Configured model: `t2_hkgai-v3_fp8_1m_e7`.
- `/v1/models` listed this alias and `HKGAI-V3`.
- Stage 1 response reported the served model as
  `t2_hkgai-v3_fp8_1m_e10_meeting`.

This may be legitimate server-side routing/aliasing. It is **not** assumed to be
a configuration error, and no model switch is made. The probe records both the
configured and response-reported model to confirm behavior.

## 6. Likely disconnect causes

Ordered by evidence:

1. **Server/gateway hard response timeout around 60–67 seconds.** The server
   disconnects consistently near this point even when the client allows 90–180s.
2. **Long reasoning on the structured planning prompt.** The model is a
   reasoning model; structured JSON planning may trigger enough reasoning to
   exceed the gateway window.
3. **Model/route mismatch or gateway policy for structured output.** The served
   model alias differs from the configured alias; the gateway may have a special
   route or timeout for structured requests.
4. **Streaming support/expectation.** Unknown until probed. If the gateway
   buffers a long non-streaming completion and closes at 60s, streaming may
   deliver incremental tokens and finish within the window.
5. **Client/proxy configuration.** Ruled out: no proxy env, HTTP/1.1, client
   timeout longer than observed disconnect.
6. **Unsupported request parameter.** Not supported by evidence; Stage 1 uses the
   same fields successfully.

## 7. Streaming implementation

Optional streaming is implemented in `OpenAICompatibleProvider` and exposed by
`HKGAIProvider(stream=...)` / `AGENTFORGE_HKGAI_STREAM`.

Properties:

- default remains non-streaming; all Phase 1–6A behavior unchanged;
- `stream=true` sends standard Chat Completions SSE;
- concatenates `delta.content`, including content-part lists;
- records `reasoning_content`/`reasoning` presence and character count, never
  merges it into user-visible content;
- parses `finish_reason` and optional usage chunks;
- tolerates missing usage (zero usage, `usage_reported=false`);
- accepts a missing `[DONE]` only when a `finish_reason` arrived, marking
  `terminal_event_missing=true`;
- rejects empty streams and streams that end without a terminal/finish signal;
- rejects malformed SSE JSON;
- raises on mid-stream transport failure after partial content instead of
  returning a partial completion;
- does not retry after content has been received; no partial JSON is accepted as
  a successful Agent Action;
- no ToolExecutor or permission changes.

Offline SSE tests: 10 tests in `tests/test_phase6a_streaming.py`, all passing
with `httpx.MockTransport`.

## 8. Prepared one-request probe

Command (not run):

```powershell
python -m agentforge benchmark hkgai-probe --provider hkgai --allow-live-api `
  --max-tokens 256 --timeout 120 --output-dir benchmarks/runs
```

Probe parameters:

| Item | Value |
|---|---|
| Model | current configured HKGAI model only |
| Prompt | exact Coordinator planning system + user prompt that failed |
| `stream` | true |
| `temperature` | 0.2 |
| `max_tokens` | 256 |
| Client timeout | 120s |
| HTTP requests | exactly 1 |
| Provider retries | 0 |
| Probe token budget | 10,000 |
| Tools / side effects | none |
| Retry on failure | none |
| API key output | redacted; never stored |

Recorded fields:

- HTTP status
- first byte / first SSE event / first content chunk latency
- SSE event count and content chunk count
- content and reasoning character counts
- finish_reason
- terminal event present/missing
- usage reported and token counts
- total elapsed
- sanitized error type/detail
- configured and response-reported model
- budget snapshot

Output: `benchmarks/runs/hkgai_probe_<timestamp>/probe.json` and
`PROBE_REPORT.md`.

No real probe has been executed. It requires a new explicit authorization because
it is an additional paid request beyond the Stage 1/2 budgets already consumed.

## 9. Questions for the course/HKGAI provider

1. Does `t2_hkgai-v3_fp8_1m_e7` intentionally route to
   `t2_hkgai-v3_fp8_1m_e10_meeting`?
2. Is there a 60-second gateway limit for non-streaming long/reasoning requests?
3. Is SSE streaming supported at `/v1/chat/completions`?
4. Should structured planning use a different approved alias/route or streaming
   mode?
5. Is there a recommended `max_tokens`/reasoning budget for structured output?

Do not switch model/provider without explicit authorization.
