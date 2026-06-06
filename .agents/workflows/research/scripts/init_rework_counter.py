#!/usr/bin/env python3
"""Reset the per-gate rework counter to zero.

Outputs JSON: {"rework_count": {"value": 0}}
"""
import json

print(json.dumps({"rework_count": {"value": 0}}))
