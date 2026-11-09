# HKGAI API 技术排查报告（可直接转发）

**项目：** AgentForge
**日期：** 2026-11-09
**Endpoint：** `https://api1.hkgai.net/v1/chat/completions`
**配置模型：** `t2_hkgai-v3_fp8_1m_e7`
**服务端成功响应中返回的模型：** `t2_hkgai-v3_fp8_1m_e10_meeting`
**API Key：** 已配置，但不在本报告中
**客户端：** Python 3.12.10 / httpx 0.28.1 / HTTP/1.1（HTTP/2 未启用）

## 1. 问题摘要

简单 Chat Completions 请求正常；但包含完整 Coordinator PLANNER_PROTOCOL 和 Agent Catalog 的规划请求，在约 58–67 秒时被服务端断开，**没有返回 HTTP 状态码、响应头、SSE 字节、Token Usage 或模型名称**。

客户端已排除：

- API Key 错误：简单请求 200；
- Base URL/路径错误：`/v1/chat/completions` 正常；
- 客户端超时：60s、90s、120s、180s 下均在约 58–67 秒由服务端断开；
- 代理：无 `HTTP_PROXY`/`HTTPS_PROXY` 等环境变量；
- 重试循环：这些诊断均为 `max_retries=0`；
- SSE 解析：Streaming 请求在收到任何 HTTP Response Header 前已断开；
- 工具/沙箱：所有诊断请求均不执行工具、不产生副作用。

## 2. 已成功请求

### 2.1 最小 Connectivity

- 方法：POST `/v1/chat/completions`
- `model`: `t2_hkgai-v3_fp8_1m_e7`
- User: `Reply with exactly OK.`
- `stream=false`
- `max_tokens=256`
- `temperature=0.2`
- 结果：HTTP 200，content=`OK`，finish_reason=`stop`
- Usage：prompt 89 + completion 36 = **125 tokens**
- 耗时：**5.979s**
- 响应中 `model`：`t2_hkgai-v3_fp8_1m_e10_meeting`

### 2.2 最小结构化 JSON（实验 A）

- System：要求只返回一个 JSON object
- User：要求返回 `{"status":"ok","value":1}`
- `stream=false`
- `max_tokens=256`
- `temperature=0.2`
- 结果：HTTP 200，content 长度 25，finish_reason=`stop`
- JSON 提取成功，Schema 严格匹配
- Usage：133 + 53 = **186 tokens**
- 耗时：**6.406s**
- 响应中 `model`：`t2_hkgai-v3_fp8_1m_e10_meeting`

## 3. 失败请求

### 3.1 完整 Coordinator Planning（原 Stage 2）

- System：`PLANNER_PROTOCOL`（1,213 字符）
- User：约 1,085 字符，包含 Agent Catalog 和 `MAX_SUBTASKS=3`
- 总 Prompt：约 **2,298 字符**
- `stream=false`
- `max_tokens` 尝试过 2048 / 512 / 256
- `temperature=0.2`
- `max_retries=0`
- 多次请求均在 **65.4–67.6 秒**断开
- HTTP 状态：**无**
- Response headers / content / usage：**无**
- 异常：`RemoteProtocolError: Server disconnected without sending a response`

### 3.2 同一 Planning Prompt，Streaming

- 完全相同的 Planning Prompt
- `stream=true`
- `max_tokens=256`
- timeout 120s
- `max_retries=0`
- 耗时 **67.437s**
- HTTP 状态：**无**
- 首个 HTTP Response Header / SSE event / content chunk：**均未收到**
- Usage：**无**
- 异常：`RemoteProtocolError: Server disconnected without sending a response`

Streaming 没有改善，因为服务端在返回任何响应头或字节之前就断开了。

### 3.3 简化用户任务、保留真实 Planner Prompt（实验 B）

- System：与 3.1 完全相同的真实 `PLANNER_PROTOCOL`
- Agent Catalog：与 3.1 相同
- 原 Stage 2 的 `MAX_SUBTASKS=3` 保持不变
- 仅把用户任务简化为：`Create notes/simple.md containing "hello".`
- 总 Prompt：**2,290 字符**
- `stream=false`
- `max_tokens=512`
- timeout 120s
- `max_retries=0`
- 耗时 **58.375s**
- HTTP 状态：**无**
- Response headers / content / usage：**无**
- 异常：`RemoteProtocolError: Server disconnected without sending a response`

实验 B 的 Prompt 只比完整 Stage 2 少 8 个字符，因此它没有真正减少 Prompt 规模；结果与 3.1 一致。

### 3.4 Compact Planner 单请求验证（新增，2026-11-09）

在用户明确授权下，额外执行了**恰好 1 次** Compact Planner 请求：

- System：Compact Planner Protocol，470 字符
- Agent Catalog：紧凑能力目录，322 字符
- 总 Prompt：**889 字符**（完整协议为 2,290 字符）
- 用户任务：`Create notes/simple.md containing "hello".`
- `MAX_SUBTASKS=3`
- `stream=false`
- `max_tokens=768`
- timeout 120s，`max_retries=0`，无工具
- 预算：1 次请求 / 10,000 tokens

结果：

- HTTP **200**，耗时 **13.5s**
- 返回模型：`t2_hkgai-v3_fp8_1m_e10_meeting`
- Usage：300 prompt + 344 completion = **644 tokens**
- `finish_reason=stop`
- 返回 1 个 `coding` 子任务，JSON 可解析，`ExecutionPlan`、`validate_plan`
  和 `CapabilityAssigner` 均通过

这说明当前 Endpoint/模型在 Prompt 缩至 889 字符时，可以完成一次合法的
单任务规划请求。但它**不能**证明完整 Stage 2 已通过：完整 Prompt 的请求仍然
全部在响应头之前断开，多任务/复杂目标、修复路径和后续 Stage 仍未真实验证。
因此 Compact 仅作为 HKGAI 推荐的可配置模式，Stage 2 整体状态仍为 FAILED。

## 4. 对照结论

| 请求 | Prompt 字符数 | HTTP | 耗时 | 结果 |
|---|---:|---:|---:|---|
| 最小 connectivity | ~100 | 200 | 5.979s | 成功，125 tokens |
| 最小 JSON（A） | 200 | 200 | 6.406s | 成功，186 tokens |
| 完整 Planner（Stage 2） | 2,298 | 无 | 65–68s | 服务端无响应断开 |
| Planner + stream | 2,298 | 无 | 67.437s | 服务端无响应断开 |
| Planner + 简单任务（B） | 2,290 | 无 | 58.375s | 服务端无响应断开 |
| Compact Planner | 889 | 200 | 13.5s | 成功，644 tokens，合法单任务计划 |

因此：

- 认证、Endpoint、Model 路由、Token Usage 和普通 JSON 解析均可工作。
- 失败与 **完整 Planner System Prompt + Agent Catalog + 规划 JSON 任务** 的组合相关；
  将 Prompt 压缩到 889 字符后的一次请求成功，说明 Prompt 规模/reasoning 时间
  很可能参与触发服务端约 60 秒的 pre-response 限制。
- 由于 B 的 Prompt 长度几乎未减少，不能把 B 的失败单独归因于“用户任务太复杂”。
- 失败发生在服务端返回任何响应之前，客户端构造和解析不是直接原因。
- Compact 只有一次成功样本，不能据此断言根因或阈值；完整 Stage 2 仍为失败状态。

## 5. 请求样例

已附脱敏、无 Key 的请求样例：

`docs/HKGAI_PLANNER_REQUEST_SAMPLE.json`

内容：

```json
{
  "model": "t2_hkgai-v3_fp8_1m_e7",
  "messages": [
    {"role": "system", "content": "<PLANNER_PROTOCOL>"},
    {"role": "user", "content": "<Agent Catalog + simple one-task goal + MAX_SUBTASKS=3>"}
  ],
  "temperature": 0.2,
  "max_tokens": 512,
  "stream": false
}
```

完整 Prompt 已包含在样例文件中，不包含 Authorization header 或 API Key。

## 6. 需要 HKGAI 支持确认的问题

1. `t2_hkgai-v3_fp8_1m_e7` 是否应路由为响应中返回的
   `t2_hkgai-v3_fp8_1m_e10_meeting`？两者是否是同一模型 alias？
2. 该 Endpoint 是否存在约 **60 秒 pre-response 超时**，特别是对较大的
   system/user prompt 或 reasoning 模型请求？
3. `/v1/chat/completions` 是否支持 `stream=true` SSE？如果支持，为什么
   Streaming 请求也收不到任何 HTTP Response Header？
4. 对结构化 Planning 请求，推荐使用哪个经授权的模型/alias/route？
5. 是否有推荐的 `max_tokens`、reasoning 预算或请求格式，以避免网关在返回前
   断开？
6. 是否可以根据请求时间、Endpoint 或账号侧日志，确认这些请求在服务端是
   超时、被拒绝还是路由失败？
7. 是否可以通过降低 Prompt 长度、关闭 reasoning 或使用专用 structured-output
   endpoint 来避免该限制？客户端侧的 Compact（889 字符）单次请求已成功，但仍需
   确认多任务 Prompt 的可用长度阈值和服务端 reasoning 预算。

## 7. 请求预算与账户影响

实际诊断请求：

- Stage 1 工作：5 次请求，376 tokens
- Stage 2 工作：4 次请求，0 tokens
- Streaming probe：1 次请求，0 tokens
- 实验 A：1 次请求，186 tokens
- 实验 B：1 次请求，0 tokens
- Compact Planner：1 次请求，644 tokens
- 合计：**13 次请求，1,206 tokens**
- 没有使用其他 Provider/模型；没有工具执行或副作用。

失败请求均无 Usage 返回，无法从客户端确认服务端是否计费。

## 8. 客户端排除证据

- HTTP/1.1，未启用 HTTP/2。
- 无代理环境变量。
- `max_retries=0`。
- 客户端 timeout 多次设为 60/90/120/180 秒，失败时间仍约 58–67 秒。
- 失败时没有 HTTP 状态码，不属于 4xx/5xx 响应解析。
- Streaming 没有收到任何 response header 或 SSE byte，不属于 SSE 事件解析问题。
- 同一 endpoint/model 下简单请求与最小 JSON 请求成功，不属于认证或基础协议问题。

如支持团队需要，我可以提供对应时间窗口、完整脱敏请求体和客户端日志。
