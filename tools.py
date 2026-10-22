"""tools.py - the Linux "tools" the LLM can call (function calling).

Every tool returns a plain dict so it can be sent back to the LLM as JSON.
Dangerous tools (kill, restart) need a two-step confirmation: the first call
only asks, and it runs only after the HUMAN has answered in a later turn.
"""
import os
import re
import signal
import subprocess
import time

from safety import PROTECTED_UNITS, SafetyError, check_safe, process_name

# ---------------------------------------------------------------------------
# Confirmation gate (prevents the LLM from confirming on the user's behalf)
# ---------------------------------------------------------------------------
STATE = {"turn": 0}          # increased once per user message by agent.py
PENDING = {}                 # (action, pid) -> turn when we asked the user


def new_turn() -> None:
    STATE["turn"] += 1


def _needs_confirmation(action: str, pid: int, text: str, confirmed: bool):
    """Return a 'needs_confirmation' dict, or None if the action may proceed."""
    key = (action, pid)
    asked_in = PENDING.get(key)
    if confirmed and asked_in is not None and asked_in < STATE["turn"]:
        del PENDING[key]
        return None                      # user said yes in a later message
    PENDING[key] = STATE["turn"]
    return {
        "needs_confirmation": True,
        "message": f"{text} Ask the user to confirm. Do NOT run this again "
                   f"until the user has answered yes.",
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ps(columns: str):
    out = subprocess.run(["ps", "-eo", columns], capture_output=True, text=True).stdout
    return out.splitlines()[1:]


def _systemd_unit(pid: int):
    """Return the systemd service name that owns pid (e.g. 'nginx.service')."""
    try:
        with open(f"/proc/{pid}/cgroup") as f:
            m = re.search(r"([^/\s]+\.service)", f.read())
            return m.group(1) if m else None
    except OSError:
        return None


def _is_zombie(pid: int) -> bool:
    return any(z["pid"] == pid for z in list_zombies()["zombies"])


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
def list_zombies() -> dict:
    """Find all zombie (state Z) processes and the parent of each."""
    zombies = []
    for line in _ps("stat,pid,ppid,comm"):
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        stat, pid, ppid, comm = parts
        if stat.startswith("Z"):
            ppid = int(ppid)
            zombies.append({
                "pid": int(pid), "name": comm, "ppid": ppid,
                "parent_name": process_name(ppid) or "unknown",
            })
    return {"count": len(zombies), "zombies": zombies}


def get_process_info(pid: int) -> dict:
    """Details about one process: state, parent, user, age, command line."""
    r = subprocess.run(
        ["ps", "-o", "pid=,ppid=,stat=,user=,etimes=", "-p", str(pid)],
        capture_output=True, text=True)
    line = r.stdout.strip()
    if not line:
        return {"error": f"Process {pid} does not exist."}
    p, pp, stat, user, age = line.split()
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmd = f.read().replace(b"\0", b" ").decode(errors="replace").strip()
    except OSError:
        cmd = ""
    return {
        "pid": int(p), "ppid": int(pp), "state": stat, "user": user,
        "age_seconds": int(age), "name": process_name(pid),
        "command": cmd or "(none - zombie or kernel thread)",
        "systemd_unit": _systemd_unit(pid),
    }


def trace_parent_chain(pid: int) -> dict:
    """Walk upward: process -> parent -> grandparent ... until PID 1."""
    chain, seen = [], set()
    while pid > 0 and pid not in seen:
        seen.add(pid)
        info = get_process_info(pid)
        if "error" in info:
            break
        chain.append({k: info[k] for k in ("pid", "name", "state", "ppid", "systemd_unit")})
        if pid == 1:
            break
        pid = info["ppid"]
    return {"chain": chain}


def list_orphans() -> dict:
    """Running processes adopted by init (PPID 1) that are NOT managed services."""
    orphans = []
    for line in _ps("pid,ppid,stat,user,etimes,comm"):
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        pid, ppid, stat, user, age, comm = parts
        pid = int(pid)
        if int(ppid) != 1 or pid == 1 or stat.startswith("Z"):
            continue
        if _systemd_unit(pid):                      # normal service, not an orphan
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                if not f.read():                    # kernel thread
                    continue
        except OSError:
            continue
        orphans.append({"pid": pid, "name": comm, "user": user,
                        "state": stat, "age_seconds": int(age)})
    return {"count": len(orphans), "orphans": orphans[:30]}


def send_sigchld(parent_pid: int) -> dict:
    """Tell a parent 'your child died, please call wait()'. Safe, non-destructive."""
    check_safe(parent_pid)
    os.kill(parent_pid, signal.SIGCHLD)
    return {"ok": True, "message": f"Sent SIGCHLD to PID {parent_pid}."}


def verify_reaped(zombie_pid: int) -> dict:
    """Check whether a zombie is gone."""
    return {"pid": zombie_pid, "still_zombie": _is_zombie(zombie_pid)}


def reap_zombie(zombie_pid: int) -> dict:
    """Step 1 of cleanup: SIGCHLD the parent, wait 2s, check if the zombie is gone."""
    if not _is_zombie(zombie_pid):
        return {"error": f"PID {zombie_pid} is not a zombie (or already gone)."}
    ppid = get_process_info(zombie_pid)["ppid"]
    if ppid == 1:
        return {"ok": True, "message": "Parent is init/systemd, which reaps zombies "
                                       "by itself. Nothing to do."}
    check_safe(ppid)
    os.kill(ppid, signal.SIGCHLD)
    time.sleep(2)
    gone = not _is_zombie(zombie_pid)
    return {"zombie_pid": zombie_pid, "parent_pid": ppid,
            "parent_name": process_name(ppid), "reaped": gone,
            "next_step": None if gone else
            "Parent ignored SIGCHLD (it is probably stuck or buggy). Offer to "
            "restart or terminate the parent."}


def reap_all_zombies() -> dict:
    """Clean ALL zombies at once: one SIGCHLD per parent, one 2-second wait in total."""
    by_parent = {}
    for z in list_zombies()["zombies"]:
        by_parent.setdefault(z["ppid"], []).append(z["pid"])
    if not by_parent:
        return {"count": 0, "message": "No zombie processes found."}
    signalled, skipped = [], []
    for ppid, pids in by_parent.items():
        if ppid == 1:
            skipped.append({"parent_pid": 1, "zombies": pids,
                            "reason": "parent is init/systemd, which reaps by itself"})
            continue
        try:
            check_safe(ppid)
            os.kill(ppid, signal.SIGCHLD)
            signalled.append(ppid)
        except SafetyError as e:
            skipped.append({"parent_pid": ppid, "zombies": pids, "reason": str(e)})
        except ProcessLookupError:
            pass                                    # parent already gone
    if signalled:
        time.sleep(2)                               # one wait for all parents
    left = {z["pid"] for z in list_zombies()["zombies"]}
    report = [{"parent_pid": p, "parent_name": process_name(p) or "unknown",
               "zombies": by_parent[p], "reaped": [x for x in by_parent[p] if x not in left],
               "still_zombie": [x for x in by_parent[p] if x in left]} for p in signalled]
    stuck = [r["parent_pid"] for r in report if r["still_zombie"]]
    return {"parents": report, "skipped": skipped, "stuck_parents": stuck,
            "next_step": ("These parents ignored SIGCHLD: offer to restart or terminate "
                          "each one.") if stuck else None}


def terminate_process(pid: int, force: bool = False, confirmed: bool = False) -> dict:
    """SIGTERM (polite) or SIGKILL (force) a process. Needs user confirmation."""
    check_safe(pid)
    if _is_zombie(pid):
        return {"error": "That PID is a zombie; it is already dead and cannot be "
                         "killed. Target its PARENT instead."}
    sig = signal.SIGKILL if force else signal.SIGTERM
    name = process_name(pid)
    gate = _needs_confirmation(
        "kill", pid, f"About to send {sig.name} to '{name}' (PID {pid}).", confirmed)
    if gate:
        return gate
    os.kill(pid, sig)
    time.sleep(1)
    return {"ok": True, "message": f"Sent {sig.name} to '{name}' (PID {pid}).",
            "still_running": os.path.exists(f"/proc/{pid}") and not _is_zombie(pid)}


def restart_service(pid: int, confirmed: bool = False) -> dict:
    """Restart the systemd service that owns this PID. Needs user confirmation."""
    check_safe(pid)
    unit = _systemd_unit(pid)
    if not unit:
        return {"error": f"PID {pid} does not belong to a systemd service. "
                         f"Use terminate_process instead."}
    if unit in PROTECTED_UNITS:
        return {"error": f"{unit} is protected and cannot be restarted by this agent."}
    gate = _needs_confirmation(
        "restart", pid, f"About to run 'systemctl restart {unit}'.", confirmed)
    if gate:
        return gate
    r = subprocess.run(["systemctl", "restart", unit], capture_output=True, text=True)
    return {"ok": r.returncode == 0, "unit": unit, "stderr": r.stderr.strip()}


# ---------------------------------------------------------------------------
# Dispatcher used by agent.py
# ---------------------------------------------------------------------------
TOOL_FUNCS = {f.__name__: f for f in (
    list_zombies, get_process_info, trace_parent_chain, list_orphans,
    send_sigchld, verify_reaped, reap_zombie, reap_all_zombies, terminate_process, restart_service,
)}


PID_ARGS = ("pid", "parent_pid", "zombie_pid")


def _bad_pid(args: dict):
    """Return an error text if any PID argument is not a positive whole number."""
    for key in PID_ARGS:
        if key in args:
            v = args[key]
            if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
                return f"Invalid value for '{key}': a PID must be a positive whole number."
    return None


def run_tool(name: str, args: dict) -> dict:
    func = TOOL_FUNCS.get(name)
    if func is None:
        return {"error": f"Unknown tool '{name}'."}
    if not isinstance(args, dict):
        return {"error": "Tool arguments must be a JSON object."}
    problem = _bad_pid(args)
    if problem:
        return {"error": problem}
    try:
        return func(**args)
    except SafetyError as e:
        return {"error": f"SAFETY BLOCK: {e}"}
    except ProcessLookupError:
        return {"error": "That process no longer exists."}
    except PermissionError:
        return {"error": "Permission denied (the agent must run as root for this)."}
    except TypeError as e:
        return {"error": f"Bad arguments for {name}: {e}"}
