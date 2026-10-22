#!/usr/bin/env python3
"""Creates zombies for testing. Parent never calls wait(), so they stay Z.

  python3 tests/make_zombie.py        # 2 zombies
  python3 tests/make_zombie.py 5      # 5 zombies
"""
import os
import sys
import time

n = int(sys.argv[1]) if len(sys.argv) > 1 else 2
for _ in range(n):
    if os.fork() == 0:
        os._exit(0)                  # child dies at once -> becomes zombie
print(f"Parent PID {os.getpid()} created {n} zombies. Ctrl+C to stop.")
time.sleep(3600)
