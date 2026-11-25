# olares-colibri

[colibri](https://github.com/JustVugg/colibri) (Apache-2.0) packaged as a native
Olares app for an Olares One (RTX 5090 Laptop 24 GB, 96 GB RAM, NVMe), serving
**Qwen3.8-Flash-Next** (`qwen38-flash-next`, `Qwen/Qwen3.8-Flash-Next-FP8` at
the revision colibri pins, ~186 GB).

- Image: `ghcr.io/wooj-commits/olares-colibri:<version>`: CUDA 12.8, colibri
  `qwen38` engine built with `CUDA=1 CUDA_ARCH=portable` (sm_80..sm_120).
- Chart: `chart/colibri` → `dist/colibri-<version>.tgz` (also attached to each
  GitHub release). Install with Market → My Olares → Upload custom app package.

## What happens on first start

1. The pod downloads the model into appData (`.../models/<model id>`) with
   colibri's own resumable, sha256-checked downloader. The container log prints a
   progress line every 15 s; the Colibri entrance shows a status page meanwhile.
2. Then it runs `coli web` (API on :8000, chat UI on :8790) with `--gpu 0
   --vram 20 --ram 56`.

Uninstalling the app deletes appData, which includes the downloaded model.

## Settings (Olares app env)

| Env | Default | Meaning |
|---|---|---|
| `COLI_MODEL_ID` | `qwen38-flash-next` | or `qwen38-flash-next-iq2xs` / `-iq3` (unsloth GGUFs, 80/88 GB, ~10-20x faster on a 24 GB card) |
| `COLI_RAM` | 56 | expert-cache RAM budget, GB |
| `COLI_VRAM` | 20 | `--vram`, GB |
| `COLI_CTX` | auto | context tokens |
| `COLI_EXTRA_ARGS` | | extra `coli web` flags |
| `COLI_API_KEY` | | optional bearer key on the API |

## Calling the API

Both entrances are `private` (Olares login). From a browser session or from a
device on LarePass VPN:

```sh
curl https://<colibriapi-entrance>.woojinc1205.olares.com/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen38-flash-next","messages":[{"role":"user","content":"Hi"}]}'
```

`GET /v1/models`, streaming (`"stream": true`), tools, images, and
`reasoning_effort` (`low|medium|high|xhigh`) or `enable_thinking: false` are supported.
