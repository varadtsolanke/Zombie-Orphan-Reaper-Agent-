# Zombie & Orphan Reaper Agent

Background service (no UI) for the Agentic Shell. Finds zombie/orphan processes,
traces parents, reaps with SIGCHLD (one tool, `reap_all_zombies`, cleans every zombie with a single 2 s wait), and (after user confirmation) restarts or
terminates a stuck parent.

## Files
| File | Purpose |
|---|---|
| agent.py | Service: Unix socket server + LLM tool loop + background scanner |
| tools.py | The Linux tools (ps, /proc, signals, systemctl) |
| tool_schemas.py | Tool descriptions the LLM reads |
| safety.py | Protected processes, blocks dangerous actions |
| tests/evaluate.py | Review 3 test harness: accuracy, latency, safety, error recovery, confirmation gate |
| zr_client.py | Terminal client (how the base shell talks to the agent) |
| tests/make_zombie.py, tests/make_orphan.py | Create test zombies / orphans |
| zombie-reaper.service | systemd unit |

## Quick test (no root, no systemd)
    export ZR_SOCKET=/tmp/zr.sock
    python3 agent.py &                         # offline mode if no API key
    python3 tests/make_zombie.py 2 &
    python3 zr_client.py "find all zombies"
    python3 zr_client.py "find all zombies and clean them up"

## Run the evaluation (Review 3)
    python3 tests/evaluate.py                      # offline baseline
    ANTHROPIC_API_KEY=sk-... python3 tests/evaluate.py   # live LLM accuracy + latency

## With the LLM
    pip install anthropic
    export ANTHROPIC_API_KEY=sk-...
    python3 agent.py

## Install as a service
    sudo mkdir -p /opt/zombie-reaper && sudo cp *.py /opt/zombie-reaper/
    sudo cp zombie-reaper.service /etc/systemd/system/
    echo 'ANTHROPIC_API_KEY=sk-...' | sudo tee /etc/zombie-reaper.env
    sudo chmod 600 /etc/zombie-reaper.env
    sudo systemctl daemon-reload
    sudo systemctl enable --now zombie-reaper
    sudo python3 /opt/zombie-reaper/zr_client.py "find all zombies and clean them up"
    journalctl -u zombie-reaper -f
