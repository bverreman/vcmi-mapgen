"""Locate the local VCMI user-data directory (Data/, Maps/, Mods/) on any OS.

Priority: the VCMI_HOME environment variable, then the platform's standard VCMI
locations (first existing wins), then the first candidate as a best-effort default
so error messages still point at a sensible path.
"""
import os
import sys


def _candidates():
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        return [os.path.join(home, "Documents", "My Games", "vcmi")]
    if sys.platform == "darwin":
        return [os.path.join(home, "Library", "Application Support", "vcmi")]
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    return [
        os.path.join(home, ".var", "app", "eu.vcmi.VCMI", "data", "vcmi"),  # flatpak
        os.path.join(xdg, "vcmi"),                                          # native
    ]


def vcmi_home():
    env = os.environ.get("VCMI_HOME")
    if env:
        return os.path.expanduser(env)
    cands = _candidates()
    for c in cands:
        if os.path.isdir(c):
            return c
    return cands[0]
