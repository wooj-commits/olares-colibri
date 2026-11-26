# Colibri API (for application engineers)

This is the contract for calling the self-hosted Colibri LLM from another app.
It describes the **live deployment** on Woojin's Olares One (`woojinc1205`), not a generic Colibri install.

| | |
|---|---|
| Upstream | [JustVugg/colibri](https://github.com/JustVugg/colibri) v2.2.0, image built at commit `e479104ae01bdc40ce81e675299b1d230891b321` |
| Olares packaging | [wooj-commits/olares-colibri](https://github.com/wooj-commits/olares-colibri) chart **0.1.1** (container image tag `0.1.0`) |
| Hardware | Olares One: RTX 5090 Laptop 24 GB, 96 GB RAM, NVMe |
| Model | Qwen3.8-Flash-Next FP8 (`Qwen/Qwen3.8-Flash-Next-FP8`), catalog id **`qwen38-flash-next`** |
| Measured speed (this host) | ~12 s to first token, ~3.5–3.7 tok/s generating, ~4.8 tok/s reading the prompt |

The engine is `coli web` (OpenAI- and Anthropic-compatible gateway on port 8000, chat UI on 8790). Behavior below is taken from that pinned gateway source and from this chart. Anything not confirmed against the running process is marked **unverified**.

---

## 1. Overview and limits

Colibri is a local inference server. It is **not** a multi-tenant public API today.

- **One generation at a time** by default (`--kv-slots` defaults to 1; the chart does not raise it). Further requests wait in a FIFO queue of up to 8, for up to 300 s, then return HTTP 429.
- The model **reasons before it answers**. Default effort is `xhigh`. Reasoning can be long, and it counts toward `max_tokens` and wall time.
- If the client omits `max_tokens` / `max_completion_tokens`, the server uses its ceiling, which with this chart is the **full context window** (the boot command does not pass `--max-tokens`). Always set a cap.
- `n` must be 1. `seed` is accepted and ignored. `frequency_penalty` / `presence_penalty`, a non-empty `logit_bias`, `best_of` > 1, and `suffix` are refused with 400.
- Request bodies must fit in **4 MiB**.
- Logprobs are not available on this engine (a `logprobs` request returns a named 400). They are implemented only for GLM and MiMo.
- Tool calling is supported. Vision is supported by this model family, with caveats below.
- During model download or engine load, `/v1/*` and `/health` return **503**.

---

## 2. Base URL, model id, and endpoints

### Public hostnames (current install)

| Role | URL |
|---|---|
| Chat UI | <https://4c98fecb0.woojinc1205.olares.com/> |
| OpenAI-compatible API | `https://4c98fecb1.woojinc1205.olares.com/v1` |
| Anthropic-compatible API | `https://4c98fecb1.woojinc1205.olares.com` (no `/v1` suffix; the path is `/v1/messages`) |

Those `4c98fecb0` / `4c98fecb1` labels are the current Olares entrance ids. They can change if the app is reinstalled. The in-cluster Service DNS in §3.1 is the stable name.

### Model id

Send exactly:

```text
qwen38-flash-next
```

The gateway 404s any other `model` value (`model_not_found`). Do not use the upstream registry default `qwen3.8-flash-next-colibri`; this chart serves the catalog id.

### Endpoints (all on the API process, port 8000)

| Method | Path | Notes |
|---|---|---|
| `GET` | `/v1/models` | OpenAI list. `data[0].id` is the model id; `context_length` and `max_model_len` are the live window; `input_modalities` is `["text"]` or `["text","image"]`. |
| `GET` | `/v1/models/qwen38-flash-next` | Same object as one list entry. |
| `POST` | `/v1/chat/completions` | Primary chat API. JSON or SSE (`"stream": true`). |
| `POST` | `/v1/completions` | Legacy completions. Exists; prefer chat. |
| `POST` | `/v1/messages` | Anthropic Messages API. |
| `POST` | `/v1/messages/count_tokens` | Returns `{"input_tokens": N}` as an **estimate** from the rendered prompt. Nothing reaches the engine. |
| `GET` | `/health` | Liveness. See §8. |
| `GET` | `/metrics` | Prometheus text. Same auth policy as the API. |

Also implemented upstream, not the usual path for this model: `POST /v1/systemone` (decision/scoring) and `POST /v1/images/generations` (refused here; this is a chat model). `GET /profile`, `/experts`, and `/power` are operator/UI telemetry.

---

## 3. How to reach it

Both Olares entrances are **`authLevel: private`**. An unauthenticated request to the public hostname is redirected (HTTP 302) to `https://auth.woojinc1205.olares.com/...`. There is **no API key configured** today (`COLI_API_KEY` is empty, and the chart therefore sets `COLI_ALLOW_INSECURE_BIND=1` so the process can bind `0.0.0.0` behind that login).

### 3.1 Same Olares (in-cluster)

The chart creates two Services in the app namespace. Olares places a per-user app in `<appid>-<owner>`, so this install is:

```text
http://colibriapi.colibri-woojinc1205.svc.cluster.local:8000/v1
```

The short form `http://colibriapi.colibri-woojinc1205:8000/v1` also resolves inside the cluster. The UI Service is `colibri.colibri-woojinc1205.svc.cluster.local:8790`.

This path **bypasses the Olares login proxy**. Because no bearer key is set, any pod that can open that Service can call the model. Do not treat in-cluster access as authenticated.

**Cross-app reachability is unverified on this cluster.** Olares automatically attaches a service-mesh sidecar and NetworkPolicy so other namespaces can call a v3 *shared* app (`options.shared: true`). Colibri is a regular per-user app, not a shared backend. If a call from another app namespace times out or hangs, a default-deny NetworkPolicy between app namespaces is the likely cause. Do not change NetworkPolicies without reporting first. Making Colibri a shared backend, or adding an allow policy, would be a chart/platform change.

### 3.2 A developer's machine (LarePass / VPN + Olares sign-in)

Use the public API hostname in §2. LarePass (or being on the same private network) gets the packet there; it does **not** skip Olares login.

What works today:

- A browser already signed into Olares, for example opening `/v1/models`.
- A tool that can reuse that signed-in session (the Olares desktop, or a client that already carries the login cookie).

A plain `curl` or SDK call from a laptop, with no Olares session, receives a 302 to the auth host and never reaches Colibri. There is no API-key path on the public hostname until §3.3 is done.

### 3.3 Outside servers (Vercel, Render, CI) — not possible today

Those platforms cannot present an Olares login cookie, so they cannot use the private entrance. **Do not ship a production client that points at this URL from Vercel or Render; it will fail with a 302.**

Enabling that would take two changes, neither of which has been made:

1. Set `COLI_API_KEY` in Olares Settings → Applications → Colibri (the chart already stores it as a Secret). When a key is set, the chart **drops** `COLI_ALLOW_INSECURE_BIND`, and the gateway requires a key (see §4).
2. Change the `colibriapi` entrance in the chart from `authLevel: private` to `public`, then upgrade the app. That is a chart change.

Until both are done, the only realistic clients are in-cluster apps that can reach the Service, and human sessions already signed into Olares.

---

## 4. Authentication

**Current deployment:** no key. The gateway accepts every request that reaches it. SDKs that refuse to start without a key can send any non-empty dummy (for example `local`); it is not checked.

**When `COLI_API_KEY` is set**, either header is accepted (constant-time compare):

```http
Authorization: Bearer <key>
```

```http
x-api-key: <key>
```

A missing or wrong key returns HTTP 401:

```json
{"error":{"message":"Invalid or missing API key.","type":"authentication_error","param":null,"code":"invalid_api_key"}}
```

`GET /health` without a valid key, when a key *is* configured, still returns a bare `{"status":"ok"}` (or 503). Hardware, queue, and context fields are only included for an authenticated request (or when no key is set). `GET /metrics` requires the key when one is configured.

---

## 5. Chat parameters

`POST /v1/chat/completions` accepts the usual OpenAI shape.

| Field | Behavior |
|---|---|
| `model` | Must be `qwen38-flash-next`. |
| `messages` | `system`, `user`, `assistant`, `tool`. A trailing non-empty `assistant` message **continues** that turn (Anthropic-style). Send `reasoning_content` back on past assistant turns so the KV prefix can be reused. |
| `stream` | `true` for SSE (`Content-Type: text/event-stream`), ending with `data: [DONE]`. |
| `stream_options.include_usage` | If `true`, a final chunk carries `usage`. |
| `max_tokens` / `max_completion_tokens` | Ceiling, not a target. Clamped to the server ceiling; a prompt that cannot fit is 400 `context_length_exceeded`. **Set this.** Reasoning plus the answer share the budget. |
| `temperature` | 0–2. Default `0.7` (`COLI_TEMP` is unset on this chart). |
| `top_p` | (0, 1]. Default `0.9`. |
| `stop` | One string, or an array of 1–4 non-empty strings. Removed from the output. |
| `tools` / `tool_choice` | Supported. See §7. |
| `response_format` | `"text"`, `"json_object"`, `"json_schema"`, or the extension `"gbnf"`. These are grammar *drafts* the engine verifies, not a hard sampling constraint. |
| `reasoning_effort` | `none`, `minimal`, `low`, `medium`, `high`, or `xhigh`. Default for this model: **`xhigh`**. `high` is treated as `xhigh`; `minimal` as `low`. `none` turns thinking off. |
| `enable_thinking` | Boolean. Default **true**. `false` pre-closes the `<think>` block so the model answers directly. Also accepted inside `chat_template_kwargs`. |
| `context_compaction` | `"auto"` or `"off"` (default). `"auto"` summarizes history when it no longer fits. |
| `cache_slot` | Integer, only relevant if `--kv-slots` is raised. Clients can omit it. |

Hosted-platform fields such as `user`, `store`, `metadata`, `service_tier`, `parallel_tool_calls`, and `web_search_options` are accepted and ignored. `seed` is accepted and has no effect.

### Context length

`COLI_CTX` is empty, so the gateway picks the largest of 16 384, 32 768, 65 536, 131 072, and 262 144 tokens (this family's maximum) that fits the memory plan. The family's fallback default is 8 192 if even 16 384 would not fit. **The live value is unverified from this box** (the entrance requires an Olares login). Read it from `GET /v1/models` (`context_length` / `max_model_len`) or authenticated `GET /health` (`context_tokens`). To pin it, set `COLI_CTX` in the app's settings.

---

## 6. Handling reasoning

On Qwen3.8-Flash-Next, thinking is **on by default** at `xhigh`. The template opens a `<think>` block; the gateway **strips the tags** and splits the stream:

- Non-streaming: `choices[0].message.reasoning_content` (reasoning) and `choices[0].message.content` (the answer).
- Streaming: `choices[0].delta.reasoning_content` first, then `choices[0].delta.content`. Markers are not leaked into `content`.

The model emits reasoning before the answer, so time-to-first-**content** token is 12 s plus however long the reasoning took at ~3.5 tok/s. Clients that only read `content` will sit silent during that period. Prefer streaming, and show or log `reasoning_content` if the user should see progress.

To turn thinking off:

```json
{"enable_thinking": false}
```

or `"reasoning_effort": "none"`, or `chat_template_kwargs: {"enable_thinking": false}`.

When you send the conversation back, include `reasoning_content` on assistant messages. Dropping it forces a full prefill of the history.

On the Anthropic path, thinking is also on by default for this model. Enable or inspect it with `{"thinking": {"type": "enabled"}}`. The reply uses Anthropic `thinking` content blocks, not `reasoning_content`.

---

## 7. Tools and images

### Tools

This engine supports OpenAI `tools` / `tool_choice` and Anthropic `tool_use` / `tool_result`. Native syntax is `<tool_call>` / `<tool_response>` XML; the gateway translates.

- `tool_choice`: `"auto"`, `"none"`, `"required"`, or a forced function object.
- `"required"` is a prompt instruction, not a sampling constraint; the model can still answer in prose.
- A forced function name is **not reliably honored** on Qwen3.8 (the gateway does not narrow the tool list). Do not assume the named tool was the only one offered.
- `strict_tool_calls` is GLM-only; on this model it returns 400.

After a `finish_reason` of `tool_calls`, send `role: "tool"` messages (OpenAI) or `tool_result` blocks (Anthropic) and call again.

### Images

The Qwen3.8 family can take **one image per request** as an OpenAI `image_url` / `input_image` part, or (in this gateway version) an Anthropic `image` block on a user message.

- Send a **base64 `data:` URI**. Remote `http(s)://` URLs are not fetched (400). Local file paths are disabled on this server (`COLI_IMAGE_ROOT` is unset).
- At most 8192×8192 pixels.
- **Unverified:** whether the official FP8 checkpoint on this host actually loaded its vision tower. If it did not, an image request returns 400 (`this engine loaded no vision tower`). Check `input_modalities` on `GET /v1/models` or `GET /health` before relying on vision.

---

## 8. Health, loading, and the queue

### While the model is loading or being downloaded

The boot wrapper answers on ports 8000 and 8790. `GET /v1/*` and `GET /health` return **503** with `Retry-After: 60`:

```json
{"error":{"message":"colibri is preparing the model","type":"model_loading","phase":"downloading","model":"qwen38-flash-next"}}
```

(The object also includes progress fields such as `line` and `uptime_s`.) The chat entrance shows an HTML status page instead.

### When the engine is up

`GET /health` returns 200 `{"status":"ok", ...}`. With no API key (the current setup), the body also includes `context_tokens`, `max_tokens`, `kv_slots`, `input_modalities`, `scheduler` counters, and `capabilities`.

If the engine process has died and is being restarted, `/health` returns **503** `{"status":"restarting"}` with `Retry-After: 5`. New chat requests wait in the queue (up to 300 s) for the new engine rather than failing immediately.

### Concurrency

Default `--max-queue 8`, `--queue-timeout 300`, `--kv-slots 1`. A request that cannot be admitted returns OpenAI-shaped **429**:

- `code: "queue_full"` — already 8 waiters.
- `code: "queue_timeout"` — waited 300 s, plus `Retry-After: 1`.

Successful responses may include the header `x-colibri-queue-wait-ms`.

---

## 9. Recommended client settings

| Setting | Recommendation |
|---|---|
| Timeouts | Long. Budget ~12 s to first token, then ~3.5 tok/s, **including reasoning**. A 2 048-token cap can take on the order of 10 minutes in the worst case. Use a 10–15 minute overall timeout, and do not treat a quiet socket during reasoning as a hang if you are not streaming. |
| Streaming | Prefer it, so you can show reasoning tokens and notice a stall. |
| `max_tokens` | Always set it (1024–2048 is a reasonable starting range unless you need more). |
| Retries | Retry **503** using `Retry-After` (60 s while loading, 5 s while the engine restarts). Retry **429** with backoff. Do not retry 400/401/404. A timed-out request may still be running on the single slot; wait or you will queue behind it. |
| `apiTimeout` | The Olares entrance sets `apiTimeout: 0` (no proxy timeout), which is required at this speed. |

---

## 10. Environment variables for client apps

```text
COLIBRI_BASE_URL=https://4c98fecb1.woojinc1205.olares.com/v1
COLIBRI_ANTHROPIC_BASE_URL=https://4c98fecb1.woojinc1205.olares.com
COLIBRI_MODEL=qwen38-flash-next
COLIBRI_API_KEY=local
```

For an in-cluster client, set `COLIBRI_BASE_URL=http://colibriapi.colibri-woojinc1205.svc.cluster.local:8000/v1` instead.

`COLIBRI_API_KEY` is a placeholder until a real key is configured. SDKs still want a non-empty string. Never commit a real key.

---

## 11. Examples

These examples assume the process can already reach the API (in-cluster, or a session that has passed Olares login). From a laptop with no login cookie they will 302.

### curl (non-streaming)

```bash
curl -sS "$COLIBRI_BASE_URL/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${COLIBRI_API_KEY:-local}" \
  -d '{
    "model": "qwen38-flash-next",
    "max_tokens": 512,
    "messages": [{"role": "user", "content": "Reply with the word pong."}]
  }'
```

Read the answer from `.choices[0].message.content` and the reasoning from `.choices[0].message.reasoning_content`.

### curl (streaming)

```bash
curl -sS -N "$COLIBRI_BASE_URL/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${COLIBRI_API_KEY:-local}" \
  -d '{
    "model": "qwen38-flash-next",
    "stream": true,
    "stream_options": {"include_usage": true},
    "max_tokens": 512,
    "enable_thinking": false,
    "messages": [{"role": "user", "content": "Reply with the word pong."}]
  }'
```

Events look like:

```text
data: {"id":"...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"reasoning_content":"..."},"finish_reason":null}]}

data: {"id":"...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"pong"},"finish_reason":null}]}

data: [DONE]
```

### curl (Anthropic)

```bash
curl -sS "$COLIBRI_ANTHROPIC_BASE_URL/v1/messages" \
  -H "Content-Type: application/json" \
  -H "x-api-key: ${COLIBRI_API_KEY:-local}" \
  -d '{
    "model": "qwen38-flash-next",
    "max_tokens": 512,
    "messages": [{"role": "user", "content": "Reply with the word pong."}]
  }'
```

`max_tokens` is required on this path, as in the Anthropic API.

### Python (official `openai` SDK)

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url=os.environ["COLIBRI_BASE_URL"],  # must include /v1
    api_key=os.environ.get("COLIBRI_API_KEY", "local"),
    timeout=900.0,
)

model = os.environ.get("COLIBRI_MODEL", "qwen38-flash-next")

completion = client.chat.completions.create(
    model=model,
    max_tokens=512,
    messages=[{"role": "user", "content": "Reply with the word pong."}],
)
message = completion.choices[0].message
print(getattr(message, "reasoning_content", None))
print(message.content)

# Streaming
with client.chat.completions.create(
    model=model,
    max_tokens=512,
    stream=True,
    messages=[{"role": "user", "content": "Reply with the word pong."}],
) as stream:
    for chunk in stream:
        delta = chunk.choices[0].delta
        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            print(reasoning, end="", flush=True)
        if delta.content:
            print(delta.content, end="", flush=True)
```

If a particular SDK version drops unknown fields, read `chunk.to_dict()["choices"][0]["delta"].get("reasoning_content")`.

### TypeScript (official `openai` SDK)

```ts
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: process.env.COLIBRI_BASE_URL, // must include /v1
  apiKey: process.env.COLIBRI_API_KEY || "local",
  timeout: 900_000,
});

const model = process.env.COLIBRI_MODEL || "qwen38-flash-next";

const completion = await client.chat.completions.create({
  model,
  max_tokens: 512,
  messages: [{ role: "user", content: "Reply with the word pong." }],
});

const message = completion.choices[0].message as OpenAI.Chat.ChatCompletionMessage & {
  reasoning_content?: string;
};
console.log(message.reasoning_content);
console.log(message.content);

const stream = await client.chat.completions.create({
  model,
  max_tokens: 512,
  stream: true,
  messages: [{ role: "user", content: "Reply with the word pong." }],
});

for await (const chunk of stream) {
  const delta = chunk.choices[0]?.delta as { content?: string; reasoning_content?: string };
  if (delta?.reasoning_content) process.stdout.write(delta.reasoning_content);
  if (delta?.content) process.stdout.write(delta.content);
}
```

### TypeScript (Vercel AI SDK, `@ai-sdk/openai-compatible`)

```ts
import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import { generateText, streamText } from "ai";

const colibri = createOpenAICompatible({
  name: "colibri",
  baseURL: process.env.COLIBRI_BASE_URL!, // must include /v1
  apiKey: process.env.COLIBRI_API_KEY || "local",
});

const model = colibri(process.env.COLIBRI_MODEL || "qwen38-flash-next");

const { text } = await generateText({
  model,
  maxTokens: 512,
  prompt: "Reply with the word pong.",
});

const result = streamText({
  model,
  maxTokens: 512,
  prompt: "Reply with the word pong.",
  // Long generations: raise the max duration in the route segment, too.
});

for await (const part of result.fullStream) {
  // `text-delta` is the answer. Whether this SDK version also surfaces
  // `reasoning_content` as a first-class part is unverified; if you need
  // the reasoning reliably, use the official openai SDK or parse SSE.
  if (part.type === "text-delta") process.stdout.write(part.textDelta ?? part.text ?? "");
}
```

Give serverless routes a long `maxDuration` (for example 300–800 s on Vercel). That only matters after the API is publicly reachable with a key; today these hosts cannot call it.

---

## 12. Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| HTTP 302 to `auth.woojinc1205.olares.com` | The entrance is private and the client has no Olares session | Use a signed-in browser, call in-cluster (§3.1), or wait for a public entrance + API key (not enabled). |
| HTTP 503, `type: "model_loading"` | Download or engine load is still in progress | Wait and retry after `Retry-After` (60 s). Check the chat entrance status page. |
| HTTP 503, `{"status":"restarting"}` | The engine process died and is being restarted | Retry after 5 s. A cold reload of this 186 GB model can take minutes. |
| HTTP 429 `queue_full` / `queue_timeout` | Another request is using the single slot, and the wait queue is full or timed out | Wait and retry. Avoid firing parallel requests. |
| HTTP 404 `model_not_found` | `model` is not exactly `qwen38-flash-next` | Fix the id. |
| HTTP 400 `context_length_exceeded` | The prompt (and reserved answer room) does not fit | Shorten history, or send `"context_compaction": "auto"`. Confirm the live window with `/v1/models`. |
| HTTP 401 `invalid_api_key` | A key has been configured and the header is missing or wrong | Send `Authorization: Bearer …` or `x-api-key`. |
| Slow or "hung" request, no error | Reasoning is running at ~3.5 tok/s before any `content` | Stream and read `reasoning_content`. Raise timeouts. Set `max_tokens`. Use `enable_thinking: false` if you want a short direct answer. |
| Image request 400 about a vision tower | The checkpoint did not load vision weights | Confirm `input_modalities` on `/v1/models`. Send text only, or reconvert with vision weights. |
| Image request 400 about a URL | Remote URLs are not fetched | Send a base64 `data:` URI. |
| Connection timeout from another Olares app | Cross-namespace policy may block the Service | See §3.1. Do not change NetworkPolicies without reporting first. |
| Empty `content`, all text in `reasoning_content` | Unusual, but can happen if a thinking split starts in the wrong mode | Check `enable_thinking`, and that you are not continuing a turn unexpectedly with a trailing assistant message. |

A quick signed-in check: open `https://4c98fecb1.woojinc1205.olares.com/v1/models` and `https://4c98fecb1.woojinc1205.olares.com/health` in a browser already logged into Olares.

---

## 13. What was not verified against the live process

The following is documented from the pinned source and this chart, but could not be confirmed with a live call from this environment (the public entrance returns 302 without an Olares session, and this environment has no cluster credentials):

- The exact live `context_length` / `context_tokens` chosen by auto context planning.
- Whether `input_modalities` currently includes `image` (vision tower actually loaded).
- Whether another Olares app namespace can open `colibriapi.colibri-woojinc1205:8000` (NetworkPolicy).
- Whether a given version of the Vercel AI SDK forwards `reasoning_content` as a first-class stream part.
- End-to-end behavior of `response_format` JSON mode and `context_compaction` on this host.

Read `/v1/models` and `/health` once you are signed in if you need those values.

---

## 14. Source of truth

- Gateway and API contract: `c/openai_server.py` and [docs/api.md](https://github.com/JustVugg/colibri/blob/e479104ae01bdc40ce81e675299b1d230891b321/docs/api.md) in JustVugg/colibri @ `e479104`.
- This package: `chart/colibri` (Services, entrances, env) and `docker/colibri_boot.py` (download 503s, `coli web` flags, served model id).
