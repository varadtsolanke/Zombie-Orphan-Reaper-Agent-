#!/usr/bin/env python3
"""Creates an orphan: parent exits, child keeps running and is adopted by init."""
import os
import time

if os.fork() == 0:
    time.sleep(600)                  # child keeps running
else:
    print("Parent exiting; child becomes an orphan (PPID 1).")
