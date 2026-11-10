# HKGAI HTTP Transport Probe (fresh connection per request)

- Mode: **LIVE**
- Outcome: **C_first_failed**
- Status: **FAILED**
- Configured model: `t2_hkgai-v3_fp8_1m_e7`
- Endpoint host: `api1.hkgai.net`
- Proxy: `http://127.0.0.1:7897` via `n/a`
- Prompt: 1376 chars, sha256[:16] `aae7d99fba17716a`, compact_action=True, tools=True
- Requests: 1; tokens: 0
- Stopped after first failure: True

## Per-request transport evidence

| # | client | proxy | conn hdr | headers ms | total s | HTTP | local addr | reused | pool | closed | tokens | status |
|---:|---|---|---|---:|---:|---:|---|---|---|---:|---:|---|
| 1 | `client@1811631068352` | `http://127.0.0.1:7897` | close | None | 119.859 | None | `n/a` | None | 0 | True | 0 | failed |

## Errors

- request 1: `RemoteProtocolError` (no_response_disconnect) after 119.859s: Server disconnected without sending a response.

## Interpretation

The first request already failed without a usable response: the route is unavailable for this request shape right now; no further request was sent.

No Authorization header or API key is recorded anywhere in this report or its JSON.
