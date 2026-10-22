"""tool_schemas.py - descriptions of the tools, sent to the LLM.

The LLM never runs code. It reads these descriptions and says
"please call <tool> with <arguments>"; agent.py then runs tools.py.
(Format below is Anthropic's. For OpenAI, rename input_schema -> parameters
and wrap each tool as {"type": "function", "function": {...}}.)
"""


def _tool(name, description, props=None, required=None):
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": props or {},
                         "required": required or []},
    }


PID = {"type": "integer"}

TOOLS = [
    _tool("list_zombies",
          "List all zombie (defunct, state Z) processes with their PID and parent PID."),
    _tool("get_process_info",
          "Get details of one process: state, parent, user, age, command, systemd unit.",
          {"pid": PID}, ["pid"]),
    _tool("trace_parent_chain",
          "Trace a process up through its parents (PPID chain) to PID 1.",
          {"pid": PID}, ["pid"]),
    _tool("list_orphans",
          "List orphan processes: running processes adopted by init (PPID 1) that are "
          "not managed systemd services."),
    _tool("send_sigchld",
          "Send SIGCHLD to a parent so it reaps its dead children. Safe.",
          {"parent_pid": PID}, ["parent_pid"]),
    _tool("reap_zombie",
          "Try to clean one zombie: sends SIGCHLD to its parent, waits 2 seconds, "
          "and reports whether the zombie is gone. ALWAYS try this first.",
          {"zombie_pid": PID}, ["zombie_pid"]),
    _tool("verify_reaped",
          "Check if a zombie PID still exists as a zombie.",
          {"zombie_pid": PID}, ["zombie_pid"]),
    _tool("restart_service",
          "Restart the systemd service that owns a PID (use on a stuck PARENT). "
          "Needs user confirmation: first call asks, call again with confirmed=true "
          "only after the user says yes in a new message.",
          {"pid": PID, "confirmed": {"type": "boolean"}}, ["pid"]),
    _tool("terminate_process",
          "Kill a process (SIGTERM, or SIGKILL if force=true). Use on a stuck PARENT, "
          "never on a zombie. Needs user confirmation: first call asks, call again with "
          "confirmed=true only after the user says yes in a new message.",
          {"pid": PID, "force": {"type": "boolean"}, "confirmed": {"type": "boolean"}},
          ["pid"]),
]
