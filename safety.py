"""safety.py - rules that stop the agent from killing something important."""
import os
import re

# Processes we must never signal, no matter what the LLM or user says.
PROTECTED_NAMES = {
    "systemd", "init", "kthreadd", "sshd", "dbus-daemon", "systemd-journal",
    "systemd-logind", "NetworkManager", "gdm", "gdm3", "lightdm", "Xorg",
    "login", "agetty",
}

# systemd services we must never restart.
PROTECTED_UNITS = {
    "ssh.service", "sshd.service", "dbus.service", "systemd-logind.service",
    "NetworkManager.service", "zombie-reaper.service", "gdm.service",
}


class SafetyError(Exception):
    """Raised when an action is blocked for safety reasons."""


def process_name(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/comm") as f:
            return f.read().strip()
    except OSError:
        return ""


def check_safe(pid: int) -> None:
    """Raise SafetyError if pid must not be touched."""
    if pid <= 2:
        raise SafetyError(f"PID {pid} is a core system process (init/kernel). Blocked.")
    if pid == os.getpid() or pid == os.getppid():
        raise SafetyError("That is the reaper agent itself (or its parent). Blocked.")
    name = process_name(pid)
    if name in PROTECTED_NAMES:
        raise SafetyError(f"'{name}' (PID {pid}) is a protected system process. Blocked.")


# ---------------------------------------------------------------------------
# Regex blacklist for destructive commands typed (or pasted) into a prompt
# ---------------------------------------------------------------------------
DANGEROUS_PATTERNS = [
    r"\brm\s+(?:-\S+\s+)*-\S*r\S*\s+(?:-\S+\s+)*/(?:\*|\s|$)",  # rm -rf /  or  /*
    r"\bmkfs(?:\.\w+)?\b",                                        # format a disk
    r"\bdd\s+if=\S+\s+of=/dev/",                                   # overwrite a device
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",                  # fork bomb
    r"\bkill\s+-9\s+(?:-1|1)\b",                                   # kill all / init
    r"\bchmod\s+-R\s+\S+\s+/(?:\s|$)",                            # open up the whole disk
    r">\s*/dev/sd[a-z]",                                            # write to a raw disk
    r"\b(?:shutdown|poweroff|reboot)\s+(?:-\S+|now)\b",           # power off the machine
    r"\b(?:delete|erase|wipe|destroy|format)\s+(?:everything|all\s+(?:my\s+|the\s+)?"
    r"(?:files|data)|(?:(?:my|the|whole|entire)\s+)+(?:drive|disk|hard\s*disk|file\s*system))",
]
_COMPILED = [re.compile(p, re.IGNORECASE) for p in DANGEROUS_PATTERNS]


def sanitize_prompt(text: str) -> None:
    """Raise SafetyError if the text contains a destructive command pattern."""
    for rx in _COMPILED:
        if rx.search(text):
            raise SafetyError("The request contains a destructive command pattern "
                              "(for example 'rm -rf /'). Blocked.")
