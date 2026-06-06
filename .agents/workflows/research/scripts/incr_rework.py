#!/usr/bin/env python3
"""Increment the per-gate rework counter.

Args:
    argv[1]  current counter value

Outputs JSON: {"rework_count": {"value": <current + 1>}}
"""
import json
import sys

current = int(float(sys.argv[1])) if len(sys.argv) > 1 and sys.argv[1] else 0
print(json.dumps({"rework_count": {"value": current + 1}}))
