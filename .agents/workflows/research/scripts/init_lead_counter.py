#!/usr/bin/env python3
"""Initialize the program-level research-lead intervention counter to zero.

This counter is cross-gate (unlike the per-gate rework counter): it bounds how
many times the research lead may revive a killed gate or pivot to a new research
direction within a single run, so a revive<->kill or pivot<->kill cycle cannot
loop forever. Set once at startup, incremented after each lead intervention.

Outputs JSON: {"lead_review_count": {"value": 0}}
"""
import json

print(json.dumps({"lead_review_count": {"value": 0}}))
