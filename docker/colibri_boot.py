#!/usr/bin/env python3
"""Olares boot wrapper for colibri.

1. Picks the catalog model COLI_MODEL_ID (default qwen38-flash-next).
2. If it is not completely on the persistent volume (COLI_MODELS_DIR/<id>),
   downloads it with colibri's own resumable, checksummed downloader, logging
   progress every LOG_EVERY seconds, while a tiny status server answers on the
   API and UI ports (so the Olares entrance shows progress, not a 502).
3. Runs the catalog's post-install steps, then execs `coli web` (OpenAI +
   Anthropic API on COLI_PORT, chat UI on COLI_UI_PORT) on the GPU.
Standard library only.
"""
import html, json, os, shlex, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.environ.get("COLIBRI_ROOT", "/opt/colibri")
C_DIR = os.path.join(ROOT, "c")
sys.path.insert(0, C_DIR)

import setup_catalog, setup_download, setup_flow  # noqa: E402

MODEL_ID = os.environ.get("COLI_MODEL_ID", "qwen38-flash-next").strip()
MODELS_DIR = os.environ.get("COLI_MODELS_DIR", "/models")
PORT = int(os.environ.get("COLI_PORT", "8000"))
UI_PORT = int(os.environ.get("COLI_UI_PORT", "8790"))
LOG_EVERY = float(os.environ.get("COLI_LOG_EVERY", "15"))

STATUS = {"phase": "starting", "model": MODEL_ID, "line": "", "error": "", "started": time.time()}


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S ") + "[boot] " + msg, flush=True)


class LogUI:
    """setup_flow's UI contract (say / progress / confirm), as throttled log lines."""
    def __init__(self):
        self._last = 0.0

    def say(self, text=""):
        if text.strip():
            log(text.strip())

    def confirm(self, question, default=True):
        return default

    def progress(self, line, force=False):
        STATUS["line"] = line.strip()
        now = time.time()
        if force or now - self._last >= LOG_EVERY:
            self._last = now
            log("download " + line.strip())


class StatusHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        if code == 503:
            self.send_header("Retry-After", "60")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        snap = dict(STATUS, uptime_s=int(time.time() - STATUS["started"]))
        if self.path.startswith("/v1") or self.path.startswith("/health") or self.path.endswith(".json"):
            return self._send(503, json.dumps({"error": {"message": "colibri is preparing the model",
                                                          "type": "model_loading", **snap}}),
                              "application/json")
        page = ("<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=15>"
                "<title>colibri: preparing</title><body style='font:15px system-ui;margin:3em'>"
                f"<h2>colibri is preparing <code>{html.escape(MODEL_ID)}</code></h2>"
                f"<p>Phase: <b>{html.escape(snap['phase'])}</b></p>"
                f"<pre>{html.escape(snap['line'])}</pre>"
                + (f"<p style='color:#b00'>Last error (retrying): {html.escape(snap['error'])}</p>" if snap['error'] else "")
                + "<p>This page refreshes every 15 s. The chat opens here once the model is ready.</p>")
        return self._send(200, page, "text/html; charset=utf-8")

    do_POST = do_GET


def start_status_servers():
    servers = []
    for port in sorted({PORT, UI_PORT}):
        try:
            srv = ThreadingHTTPServer(("0.0.0.0", port), StatusHandler)
        except OSError as e:
            log(f"status server on :{port} not started: {e}")
            continue
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
    return servers


def entry_for(model_id):
    for e in setup_catalog.CATALOG:
        if e.id == model_id:
            return e
    ids = ", ".join(e.id for e in setup_catalog.CATALOG)
    sys.exit(f"[boot] unknown COLI_MODEL_ID {model_id!r}; catalog ids: {ids}")


def ensure_model(entry, model_dir):
    ui = LogUI()
    if setup_download.is_complete(model_dir):
        log(f"{entry.id}: already complete in {model_dir}")
    else:
        STATUS["phase"] = "downloading"
        log(f"{entry.id}: {entry.repo}@{entry.revision or 'main'} ({entry.disk_gb:.0f} GB) -> {model_dir}")
        if os.environ.get("HF_ENDPOINT"):
            log(f"using HF_ENDPOINT={os.environ['HF_ENDPOINT']}")
        delay = 30
        while True:
            try:
                setup_flow.download_model(ui, entry, model_dir,
                                          verify=os.environ.get("COLI_VERIFY", "1") != "0")
                STATUS["error"] = ""
                break
            except Exception as e:  # network drops, mirror hiccups: resume
                STATUS["error"] = str(e)[:500]
                fails = STATUS.get("fails", 0) + 1
                STATUS["fails"] = fails
                hf = os.environ.get("HF_ENDPOINT", "")
                if fails >= 3 and hf and "huggingface.co" not in hf:
                    log(f"{hf} keeps failing; falling back to https://huggingface.co")
                    os.environ.pop("HF_ENDPOINT", None)
                log(f"download error: {e}; resuming in {delay}s")
                time.sleep(delay)
                delay = min(delay * 2, 600)
    STATUS["phase"] = "post-install"
    setup_flow.run_post_install(entry, C_DIR, model_dir, ui)


def main():
    entry = entry_for(MODEL_ID)
    model_dir = os.path.join(MODELS_DIR, entry.id)
    os.makedirs(model_dir, exist_ok=True)
    servers = start_status_servers()
    ensure_model(entry, model_dir)
    STATUS["phase"] = "loading engine"
    for s in servers:
        s.shutdown(); s.server_close()

    args = [sys.executable, os.path.join(C_DIR, "coli"), "web",
            "--model", model_dir, "--host", "0.0.0.0", "--port", str(PORT),
            "--ui-port", str(UI_PORT), "--no-browser",
            "--model-id", os.environ.get("COLI_SERVED_NAME", entry.id)]
    gpu = os.environ.get("COLI_GPU", "0").strip()
    if gpu:
        args += ["--gpu", gpu]
    if os.environ.get("COLI_VRAM", "").strip() and gpu != "none":
        args += ["--vram", os.environ["COLI_VRAM"].strip()]
    if os.environ.get("COLI_RAM", "").strip():
        args += ["--ram", os.environ["COLI_RAM"].strip()]
    if os.environ.get("COLI_CTX", "").strip():
        args += ["--ctx", os.environ["COLI_CTX"].strip()]
    args += shlex.split(os.environ.get("COLI_EXTRA_ARGS", ""))
    os.environ.setdefault("COLI_ALLOWED_HOSTS", "*")  # behind the Olares auth proxy
    log("exec: " + " ".join(shlex.quote(a) for a in args))
    os.chdir(C_DIR)
    os.execv(sys.executable, args)


if __name__ == "__main__":
    main()
