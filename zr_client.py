#!/usr/bin/env python3
"""zr_client.py - tiny terminal client. Shows how the base shell talks to the agent.

  python3 zr_client.py "find all zombies and clean them up"
  python3 zr_client.py            # interactive mode
"""
import json
import os
import socket
import sys

SOCKET_PATH = os.environ.get("ZR_SOCKET", "/run/zombie-reaper/agent.sock")


def ask(prompt: str) -> str:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(SOCKET_PATH)
        s.sendall((json.dumps({"prompt": prompt}) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
    return json.loads(data)["reply"]


if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(ask(" ".join(sys.argv[1:])))
    else:
        while True:
            try:
                q = input("zr> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if q:
                print(ask(q))
