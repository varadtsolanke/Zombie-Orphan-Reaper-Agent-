#!/usr/bin/env python3
"""agent.py - Zombie & Orphan Reaper Agent (background service, no UI).

What it does
  1. Listens on a Unix socket. The Agentic Shell sends {"prompt": "..."} and
     gets {"reply": "..."} back (one JSON object per line).
  2. Sends the prompt + tool list to an LLM. The LLM picks tools, we run them
     (tools.py), and feed the results back until the LLM gives a final answer.
  3. In the background, scans for zombies every SCAN_INTERVAL seconds and logs.

Run by systemd (see zombie-reaper.service). Logs go to stderr -> journalctl.
"""
import json
import logging
import os
import socketserver
import threading
import time

import tools
from safety import SafetyError, sanitize_prompt
from tool_schemas import TOOLS

SOCKET_PATH = os.environ.get("ZR_SOCKET", "/run/zombie-reaper/agent.sock")
SCAN_INTERVAL = int(os.environ.get("ZR_SCAN_INTERVAL", "30"))
MODEL = os.environ.get("ZR_MODEL", "claude-sonnet-5-5")
MAX_STEPS = 8                      # max tool-call rounds per user message

logging.basicConfig(level=logging.INFO, format="%(asctime)s [zombie-reaper] %(message)s")
log = logging.getLogger("zr")

SYSTEM_PROMPT = """You are the Zombie & Orphan Reaper Agent inside a Linux shell.
Rules:
- A zombie is already dead and CANNOT be killed. It is removed only when its parent
  calls wait(). So work on the PARENT, never try to kill the zombie itself.
- Cleanup order: (1) reap_zombie (SIGCHLD to parent). (2) If it fails, explain which
  parent is stuck (use trace_parent_chain / get_process_info) and OFFER to restart
  the service or terminate the parent. Prefer restart_service for systemd services,
  SIGTERM before SIGKILL.
- Never run restart_service or terminate_process with confirmed=true unless the user
  has just said yes in their latest message.
- Orphans (PPID 1) are normal and harmless. Only report them; kill only if asked.
- Be short and clear. Mention PIDs and names. Plain English, no markdown tables."""

# Short memory of the conversation (plain text only) so "yes" works as a follow-up.
HISTORY: list = []
HISTORY_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# LLM loop (Anthropic SDK). Only this function is LLM-specific; to use another
# provider, change this function and the format in tool_schemas.py.
# ---------------------------------------------------------------------------
def ask_llm(prompt: str) -> str:
    import anthropic
    client = anthropic.Anthropic()          # reads ANTHROPIC_API_KEY
    messages = HISTORY[-12:] + [{"role": "user", "content": prompt}]

    for _ in range(MAX_STEPS):
        resp = client.messages.create(
            model=MODEL, max_tokens=1000, system=SYSTEM_PROMPT,
            tools=TOOLS, messages=messages)

        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content if b.type == "text").strip()

        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for block in resp.content:
            if block.type == "tool_use":
                log.info("TOOL %s %s", block.name, json.dumps(block.input))
                out = tools.run_tool(block.name, block.input)
                log.info("  -> %s", json.dumps(out)[:300])
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(out)})
        messages.append({"role": "user", "content": results})

    return "I stopped after too many steps. Please try a simpler request."


def offline_answer(prompt: str) -> str:
    """Fallback with no LLM (no API key). Handy for testing the tools."""
    p = prompt.lower()
    if "orphan" in p:
        return json.dumps(tools.run_tool("list_orphans", {}), indent=2)
    if "zombie" in p:
        z = tools.run_tool("list_zombies", {})
        if not z["count"] or not any(w in p for w in ("clean", "reap", "kill", "fix")):
            return json.dumps(z, indent=2)
        lines = [json.dumps(tools.run_tool("reap_zombie", {"zombie_pid": x["pid"]}))
                 for x in z["zombies"]]
        return "\n".join(lines)
    return "Offline mode: ask about 'zombies' or 'orphans'."


def handle_prompt(prompt: str) -> str:
    with HISTORY_LOCK:                   # one conversation at a time
        tools.new_turn()
        try:
            sanitize_prompt(prompt)      # regex blacklist runs BEFORE the LLM
            if os.environ.get("ANTHROPIC_API_KEY"):
                reply = ask_llm(prompt)
            else:
                reply = offline_answer(prompt)
        except SafetyError as e:         # destructive command pattern found
            log.warning("BLOCKED prompt %r: %s", prompt, e)
            reply = f"SAFETY BLOCK: {e}"
        except Exception as e:           # network down, bad key, etc.
            log.exception("LLM error")
            reply = f"Agent error: {e}"
        HISTORY.append({"role": "user", "content": prompt})
        HISTORY.append({"role": "assistant", "content": reply or "(no reply)"})
        del HISTORY[:-20]
        return reply


# ---------------------------------------------------------------------------
# Unix socket server
# ---------------------------------------------------------------------------
class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        for line in self.rfile:                      # one JSON object per line
            try:
                prompt = json.loads(line)["prompt"]
                log.info("PROMPT %r", prompt)
                reply = handle_prompt(prompt)
            except (ValueError, KeyError):
                reply = 'Bad request. Send {"prompt": "..."} as one JSON line.'
            self.wfile.write((json.dumps({"reply": reply}) + "\n").encode())


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


# ---------------------------------------------------------------------------
# Background scanner (the "agent keeps watching" part)
# ---------------------------------------------------------------------------
def scanner_loop():
    last = set()
    while True:
        try:
            now = {(z["pid"], z["ppid"]) for z in tools.list_zombies()["zombies"]}
            if now and now != last:
                log.warning("ZOMBIES DETECTED: %s", sorted(now))
            last = now
        except Exception:
            log.exception("scanner error")
        time.sleep(SCAN_INTERVAL)


def main():
    os.makedirs(os.path.dirname(SOCKET_PATH), exist_ok=True)
    if os.path.exists(SOCKET_PATH):
        os.remove(SOCKET_PATH)
    threading.Thread(target=scanner_loop, daemon=True).start()
    with Server(SOCKET_PATH, Handler) as srv:
        os.chmod(SOCKET_PATH, 0o660)                 # only root / group can talk to it
        mode = "LLM" if os.environ.get("ANTHROPIC_API_KEY") else "OFFLINE (no API key)"
        log.info("Agent ready on %s [%s mode]", SOCKET_PATH, mode)
        srv.serve_forever()


if __name__ == "__main__":
    main()
