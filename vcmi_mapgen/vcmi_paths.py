"""Locate the local VCMI user-data directory (Data/, Maps/, Mods/) on any OS.

Priority: the VCMI_HOME environment variable, then the platform's standard VCMI
locations (first existing wins), then the first candidate as a best-effort default
so error messages still point at a sensible path.
"""
import os
import sys


def _wsl_windows_home():
    """Return the Windows user home via /mnt/c/Users when running inside WSL, else None."""
    try:
        with open("/proc/version") as f:
            if "microsoft" not in f.read().lower():
                return None
    except OSError:
        return None
    mnt = "/mnt/c/Users"
    if not os.path.isdir(mnt):
        return None
    # prefer the username that matches the current Linux user
    me = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    for name in ([me] if me else []) + sorted(os.listdir(mnt)):
        candidate = os.path.join(mnt, name)
        if os.path.isdir(candidate) and name not in ("Public", "Default", "All Users"):
            return candidate
    return None


def _candidates():
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        return [os.path.join(home, "Documents", "My Games", "vcmi")]
    if sys.platform == "darwin":
        return [os.path.join(home, "Library", "Application Support", "vcmi")]
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    cands = [
        os.path.join(home, ".var", "app", "eu.vcmi.VCMI", "data", "vcmi"),  # flatpak
        os.path.join(xdg, "vcmi"),                                          # native
    ]
    win_home = _wsl_windows_home()
    if win_home:
        cands.append(os.path.join(win_home, "Documents", "My Games", "vcmi"))
    return cands


def vcmi_home():
    env = os.environ.get("VCMI_HOME")
    if env:
        return os.path.expanduser(env)
    cands = _candidates()
    for c in cands:
        if os.path.isdir(c):
            return c
    return cands[0]


def vcmi_config_dirs():
    """Directories that contain VCMI's core config/ tree (config/objects, config/creatures, …).

    On a flatpak install this is the read-only share directory; on Windows / WSL it lives
    under AppData/Roaming/VCMI.  Returns all existing candidates so callers can search
    across them — an empty list when none are found (no VCMI install detected).
    """
    candidates = [
        "/var/lib/flatpak/app/eu.vcmi.VCMI/current/active/files/share/vcmi/config",
    ]
    if sys.platform == "win32":
        home = os.path.expanduser("~")
        candidates.append(os.path.join(home, "AppData", "Roaming", "VCMI", "config"))
    win_home = _wsl_windows_home()
    if win_home:
        candidates.append(os.path.join(win_home, "AppData", "Roaming", "VCMI", "config"))
    return [c for c in candidates if os.path.isdir(c)]
