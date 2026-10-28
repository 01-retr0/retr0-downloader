#!/usr/bin/env python3
"""Retr0 Downloader: desktop GUI around yt-dlp. Created By Retr0."""
# Copyright (C) Retr0
# Licensed under the GNU General Public License v3.0. See the LICENSE file.
import hashlib
import configparser
import json
import os
import platform
import queue
import random
import re
import shutil
import stat
import subprocess
import sys
import threading
import traceback
import uuid
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# A windowed (no console) build has no stdio; yt-dlp expects it to exist.
for _name, _mode in (("stdout", "w"), ("stderr", "w"), ("stdin", "r")):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, _mode))

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

try:
    from app_version import REPO, VERSION  # written by the GitHub build
except Exception:
    VERSION, REPO = "dev", ""

# In the packaged app, a newer yt-dlp downloaded by "Update packages" lives in LIB_DIR and
# takes priority over the copy bundled in the exe. LIB_DIR is wiped whenever the app itself
# is updated, so a new release never runs on top of an older downloaded package.
_BASE = (Path(os.environ.get("APPDATA", Path.home())) if platform.system() == "Windows"
         else Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")))
LIB_DIR = _BASE / "pydownload" / "lib"


def _rm_path(p: Path) -> None:
    try:
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists() or p.is_symlink():
            p.unlink()
    except OSError:
        pass


def _rollback_lib(new: list, old: list) -> None:
    """Undo a half-finished package install: drop the new files, put the old ones back."""
    backup = LIB_DIR / ".backup"
    for n in new:
        if n in old and not (backup / n).exists():
            continue  # the old copy was never moved away, so it is still in place
        _rm_path(LIB_DIR / n)
    for n in old:
        src = backup / n
        if src.exists() or src.is_symlink():
            _rm_path(LIB_DIR / n)
            shutil.move(str(src), str(LIB_DIR / n))


def _early_log(msg: str) -> None:
    """debug_log() is defined further down; this one is safe to call while the module is importing."""
    try:
        LIB_DIR.parent.mkdir(parents=True, exist_ok=True)
        with (LIB_DIR.parent / "debug.log").open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} {msg}\n")
    except Exception:
        pass


def _recover_lib() -> None:
    """If the app was closed in the middle of a package install, roll that install back."""
    journal = LIB_DIR / ".journal.json"
    try:
        if journal.exists():
            data = json.loads(journal.read_text(encoding="utf-8"))
            safe = lambda key: [n for n in data.get(key, []) if n and Path(n).name == n]  # noqa: E731
            _rollback_lib(safe("new"), safe("old"))
            journal.unlink()
        shutil.rmtree(LIB_DIR / ".backup", ignore_errors=True)
        shutil.rmtree(LIB_DIR / ".tmp", ignore_errors=True)
    except Exception as e:
        # The old packages cannot be restored: drop them so the bundled copy is used.
        _early_log(f"_recover_lib: rollback failed, downloaded packages removed: {e}")
        shutil.rmtree(LIB_DIR, ignore_errors=True)


if getattr(sys, "frozen", False):
    _recover_lib()
    try:
        _marker = LIB_DIR / ".for_app"
        if LIB_DIR.exists() and (not _marker.exists() or _marker.read_text().strip() != VERSION):
            shutil.rmtree(LIB_DIR, ignore_errors=True)
    except Exception:
        pass
    if LIB_DIR.exists():
        sys.path.insert(0, str(LIB_DIR))

try:
    import yt_dlp
    import yt_dlp.version  # noqa: F401
except Exception as _imp_err:
    # A broken downloaded package must never stop the app: drop it and use the bundled copy.
    _early_log(f"downloaded packages failed to import and were removed: {_imp_err}")
    shutil.rmtree(LIB_DIR, ignore_errors=True)
    sys.path[:] = [x for x in sys.path if x != str(LIB_DIR)]
    for _m in list(sys.modules):
        if _m.split(".")[0] in ("yt_dlp", "yt_dlp_ejs"):
            del sys.modules[_m]
    try:
        import yt_dlp
        import yt_dlp.version  # noqa: F401
    except ImportError:
        _r = tk.Tk()
        _r.withdraw()
        messagebox.showerror(
            "Retr0 Downloader",
            "The yt-dlp package is missing.\n\nInstall it with:\n    pip install -r requirements.txt",
        )
        sys.exit(1)

APP_NAME = "Retr0 Downloader"
FROZEN = bool(getattr(sys, "frozen", False))
PIP_PACKAGES = ["yt-dlp[default,curl-cffi]", "yt-dlp-ejs", "deno", "imageio-ffmpeg", "certifi"]
if sys.platform.startswith("linux"):
    PIP_PACKAGES.append("secretstorage")
IS_WIN = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
IS_LINUX = sys.platform.startswith("linux")
# Release builds exist only for Windows (.exe) and Linux (*-linux).
CAN_SELF_UPDATE = IS_WIN or IS_LINUX

# ======================================
# Theme
# ======================================
BG = "#07070d"
BG2 = "#05050a"
PANEL = "#0b0b14"
LINE = "#1f2937"
TEXT = "#e2e8f0"
DIM = "#64748b"
GREEN = "#00ff9c"
GREEN_HI = "#5cffc4"
CYAN = "#00e5ff"
PINK = "#ff2d6f"
YELLOW = "#facc15"
GRADIENT = ["#00ff9c", "#00f0c8", "#00e5ff", "#00b8ff", "#7a5cff", "#ff2d6f", "#ff2d6f"]

SCALE = 1.0


def px(n: float) -> int:
    return int(round(n * SCALE))


# ======================================
# Settings and environment helpers
# ======================================
def app_dir() -> Path:
    if IS_WIN:
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "pydownload"


_SAFE_QUERY_KEYS = {"v", "list", "t", "index", "id"}
_URL_RE = re.compile(r"[a-z][a-z0-9+.-]*://[^\s\"'<>)\]]+", re.I)
_CREDS_RE = re.compile(r"^([a-z][a-z0-9+.-]*://)[^/\s?#]*@", re.I)


def _redact_url(m) -> str:
    s = m.group(0)
    trail = ""
    while s and s[-1] in ".,;:":
        trail = s[-1] + trail
        s = s[:-1]
    s = _CREDS_RE.sub(r"\1***@", s)  # user:password@host
    s = s.partition("#")[0]  # fragments can carry tokens
    base, sep, query = s.partition("?")
    if sep and query:
        parts = []
        for p in query.split("&"):
            k, eq, _ = p.partition("=")
            if eq and k.lower() in _SAFE_QUERY_KEYS:
                parts.append(p)
            else:
                parts.append(f"{k}=***" if eq else "***")
        s = base + "?" + "&".join(parts)
    return s + trail


_BARE_CREDS_RE = re.compile(r"(?<![\w/@.%+-])[\w.%+-]+:[^\s/@:]+@(?=[\w.-])")  # user:pass@host
_AUTH_HEADER_RE = re.compile(
    r"\b(proxy-authorization|authorization|set-cookie|cookie|x-api-key)(\s*[:=]\s*)[^\r\n]*", re.I)
_BEARER_RE = re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{8,}", re.I)
_SECRET_KV_RE = re.compile(
    r"\b(password|passwd|pwd|passphrase|secret|token|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"session[_-]?id|sessionid)(\s*[=:]\s*)[^\s&\"',;]+", re.I)


def _home_variants() -> list:
    try:
        home = str(Path.home())
    except Exception:
        return []
    if len(home) < 4:
        return []
    forms = {home, home.replace("\\", "/"), home.replace("/", "\\"), home.replace("\\", "\\\\")}
    return sorted(forms, key=len, reverse=True)


def redact(text) -> str:
    """Hide credentials and personal data before text is shown or saved: proxy passwords, tokens and
    signed-URL parameters, auth headers, password=/token= pairs, and the home folder (shown as ~).
    Only a few harmless query keys (v, list, t, index, id) are kept so links stay recognisable.
    This is best-effort: a secret inside a URL path cannot be recognised."""
    s = _URL_RE.sub(_redact_url, str(text))
    s = _BARE_CREDS_RE.sub("***@", s)
    s = _AUTH_HEADER_RE.sub(lambda m: m.group(1) + m.group(2) + "***", s)
    s = _BEARER_RE.sub("Bearer ***", s)
    s = _SECRET_KV_RE.sub(lambda m: m.group(1) + m.group(2) + "***", s)
    for v in _home_variants():
        s = re.sub(re.escape(v) + r"(?![\w-])", "~", s, flags=re.I if IS_WIN else 0)
    return s


def redact_proxy(addr: str) -> str:
    """A proxy address without credentials (host:port stays as the user typed it)."""
    addr = str(addr)
    if "://" in addr:
        return redact(addr)
    return redact("x://" + addr)[4:]


def debug_log(msg: str) -> None:
    """Append a line to debug.log instead of silently swallowing an error."""
    try:
        d = app_dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / "debug.log").open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} {redact(msg)}\n")
    except Exception:
        pass


def set_window_icon(root) -> None:
    """Windows only: use icon.ico (bundled next to the exe) for the title bar and taskbar."""
    if not IS_WIN:
        return
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    candidates = [base / "icon.ico", Path(__file__).resolve().parent / "icon.ico"]
    if FROZEN:
        candidates.append(Path(sys.executable))  # the icon embedded in the exe itself
    for cand in candidates:
        try:
            if cand.is_file():
                root.iconbitmap(default=str(cand))
                return
        except Exception as e:
            debug_log(f"icon {cand}: {e}")
            continue


def default_out_dir() -> Path:
    return Path.home() / "Downloads" / "Retr0Downloader"


def default_profile() -> str:
    """Return a detected Opera GX profile path, or an empty string."""
    for root in browser_profile_roots().get("opera", []):
        profiles = _profiles_under(root, "chromium")
        if profiles:
            return str(profiles[0])
        if root.exists():
            return str(root)
    return ""


BROWSERS = ["opera", "chrome", "edge", "firefox", "brave", "vivaldi", "chromium"]


def browser_profile_roots() -> dict:
    """Known browser data roots on Windows, macOS, Linux, and common Flatpaks."""
    h = Path.home()
    roots = {name: [] for name in BROWSERS}
    if IS_WIN:
        local = Path(os.environ.get("LOCALAPPDATA", h / "AppData/Local"))
        roaming = Path(os.environ.get("APPDATA", h / "AppData/Roaming"))
        roots["chrome"] += [local / "Google/Chrome/User Data"]
        roots["edge"] += [local / "Microsoft/Edge/User Data"]
        roots["brave"] += [local / "BraveSoftware/Brave-Browser/User Data"]
        roots["vivaldi"] += [local / "Vivaldi/User Data"]
        roots["chromium"] += [local / "Chromium/User Data"]
        roots["opera"] += [roaming / "Opera Software/Opera Stable", roaming / "Opera Software/Opera GX Stable"]
        roots["firefox"] += [roaming / "Mozilla/Firefox", roaming / "Mozilla/Firefox/Profiles"]
    elif IS_MAC:
        base = h / "Library/Application Support"
        roots["chrome"] += [base / "Google/Chrome"]
        roots["edge"] += [base / "Microsoft Edge"]
        roots["brave"] += [base / "BraveSoftware/Brave-Browser"]
        roots["vivaldi"] += [base / "Vivaldi"]
        roots["chromium"] += [base / "Chromium"]
        roots["opera"] += [base / "com.operasoftware.Opera", base / "com.operasoftware.OperaGX"]
        roots["firefox"] += [h / "Library/Application Support/Firefox", h / "Library/Application Support/Firefox/Profiles"]
    else:
        base = h / ".config"
        roots["chrome"] += [base / "google-chrome", base / "google-chrome-beta", h / ".var/app/com.google.Chrome/config/google-chrome"]
        roots["edge"] += [base / "microsoft-edge", h / ".var/app/com.microsoft.Edge/config/microsoft-edge"]
        roots["brave"] += [base / "BraveSoftware/Brave-Browser", h / ".var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser"]
        roots["vivaldi"] += [base / "vivaldi"]
        roots["chromium"] += [base / "chromium", h / ".var/app/org.chromium.Chromium/config/chromium"]
        roots["opera"] += [base / "opera", base / "opera-gx", h / ".var/app/com.opera.opera/config/opera", h / ".var/app/com.opera.opera-gx/config/opera-gx"]
        roots["firefox"] += [
            h / ".mozilla/firefox",
            h / ".var/app/org.mozilla.firefox/.mozilla/firefox",
            h / "snap/firefox/common/.mozilla/firefox",
            h / "snap/firefox/current/.mozilla/firefox",
            h / "snap/firefox/common/.mozilla",
            h / "snap/firefox/current/.mozilla",
        ]
    return roots


def _profiles_under(root: Path, browser_kind: str) -> list:
    """Find browser profiles with cookie stores, including configured Firefox/Snap profiles."""
    found = []
    try:
        if not root.is_dir():
            return found
        if browser_kind == "firefox":
            # Firefox profile locations are configurable; discover them from profiles.ini
            # as well as checking common directory layouts.
            ini_candidates = [root / "profiles.ini", root.parent / "profiles.ini"]
            for ini in ini_candidates:
                if not ini.is_file():
                    continue
                parser = configparser.ConfigParser()
                try:
                    parser.read(ini, encoding="utf-8")
                    for section in parser.sections():
                        if not section.lower().startswith("profile"):
                            continue
                        rel = parser.get(section, "Path", fallback="").strip()
                        if not rel:
                            continue
                        is_relative = parser.getboolean(section, "IsRelative", fallback=True)
                        profile = (ini.parent / rel) if is_relative else Path(rel)
                        profile = profile.expanduser()
                        if (profile / "cookies.sqlite").is_file():
                            found.append(profile)
                except (OSError, configparser.Error, ValueError) as e:
                    debug_log(f"profiles.ini {ini}: {e}")
            if (root / "cookies.sqlite").is_file():
                found.append(root)
            for db in root.glob("*/cookies.sqlite"):
                found.append(db.parent)
            for db in root.glob("Profiles/*/cookies.sqlite"):
                found.append(db.parent)
        else:
            for profile in [root / "Default", *sorted(root.glob("Profile *")), root]:
                if not profile.is_dir():
                    continue
                if (profile / "Network/Cookies").is_file() or (profile / "Cookies").is_file():
                    found.append(profile)
    except OSError as e:
        debug_log(f"profiles under {root}: {e}")
    # Keep order, remove duplicates.
    return list(dict.fromkeys(found))


def detect_browser_cookie_sources(preferred_browser="", preferred_profile="", all_browsers=False) -> list:
    """Return ordered yt-dlp browser-cookie tuples for profiles found on this PC.
    By default only profiles of the browser the user selected are returned. Cookies of the
    other browsers are included only when all_browsers is True (an explicit opt-in)."""
    sources = []
    seen = set()

    def add(browser, profile=""):
        if browser not in BROWSERS:
            return
        item = (browser, str(profile)) if profile else (browser,)
        if item not in seen:
            seen.add(item)
            sources.append(item)

    # Respect an explicit profile first, even if its cookie database is not detectable by filename.
    if preferred_browser and preferred_profile:
        add(preferred_browser, preferred_profile)
    roots = browser_profile_roots()
    order = [preferred_browser] if preferred_browser in BROWSERS else []
    if all_browsers:
        order += [b for b in BROWSERS if b not in order]
    for browser in order:
        for root in roots.get(browser, []):
            for profile in _profiles_under(root, "firefox" if browser == "firefox" else "chromium"):
                add(browser, profile)
    return sources


def load_settings() -> dict:
    cfg = {
        "out_dir": str(default_out_dir()),
        "proxy": "127.0.0.1:10808",
        "use_proxy": False,
        "use_cookies": False,
        "cookie_all_browsers": False,
        "browser": "opera",
        "profile": default_profile(),
        "audio_only": False,
    }
    try:
        with (app_dir() / "settings.json").open("r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        debug_log(f"load_settings: {e}")
    return cfg


def save_settings(cfg: dict) -> None:
    try:
        d = app_dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / "settings.json").open("w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        debug_log(f"save_settings: {e}")


def normalize_proxy(addr: str) -> str:
    """Accept host:port or a full URL (http://, https://, socks5://, socks5h://)."""
    addr = addr.strip()
    if not addr:
        return ""
    return addr if "://" in addr else f"http://{addr}"


def user_agent() -> str:
    if IS_WIN:
        plat = "Windows NT 10.0; Win64; x64"
    elif IS_MAC:
        plat = "Macintosh; Intel Mac OS X 10_15_7"
    else:
        plat = "X11; Linux x86_64"
    return (
        f"Mozilla/5.0 ({plat}) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    )


def find_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return ""


def find_deno() -> str:
    found = shutil.which("deno")
    if found:
        return found
    # The build bundles the binary in deno_bin/: a plain "deno" file would clash with the deno/ package folder.
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        bundled = Path(meipass) / "deno_bin" / ("deno.exe" if IS_WIN else "deno")
        if bundled.exists():
            return str(bundled)
    try:
        import deno
        return str(deno.find_deno_bin())
    except Exception:
        return ""


def host_env() -> dict:
    """Environment for system programs (zenity, xdg-open). A PyInstaller app points
    LD_LIBRARY_PATH at its own libraries, which makes those programs crash."""
    env = dict(os.environ)
    if "LD_LIBRARY_PATH_ORIG" in env:
        env["LD_LIBRARY_PATH"] = env["LD_LIBRARY_PATH_ORIG"]
    else:
        env.pop("LD_LIBRARY_PATH", None)
    return env


def open_folder(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        if IS_WIN:
            os.startfile(str(path))  # noqa: S606
        elif IS_MAC:
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(
                ["xdg-open", str(path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=host_env(),
            )
        return True
    except Exception as e:
        debug_log(f"open_folder: {e}")
        return False


_ANSI = re.compile(r"\x1b\[[0-9;]*m|\[[0-9;]*m")


def clean(msg: str) -> str:
    return _ANSI.sub("", str(msg))


_RATE_HINTS = ("rate-limit", "rate limit", "too many requests", "http error 429")
_LOGIN_HINTS = ("login", "log in", "sign in", "authentication", "not a bot")
_NET_HINTS = ("timed out", "timeout", "curl: (28)", "curl: (6)", "curl: (7)", "could not resolve",
              "name or service not known", "network is unreachable", "connection refused",
              "connection reset", "unable to connect to proxy", "proxyerror")


def classify_error(msg: str) -> str:
    """'rate', 'login', 'rate_login' (the site cannot tell which), 'network', 'ffmpeg' or ''."""
    low = clean(msg).lower()
    rate = any(k in low for k in _RATE_HINTS)
    login = any(k in low for k in _LOGIN_HINTS)
    if rate and login:
        return "rate_login"
    if rate:
        return "rate"
    if login:
        return "login"
    if any(k in low for k in _NET_HINTS):
        return "network"
    if "ffmpeg" in low:
        return "ffmpeg"
    return ""


def error_hint(msg: str) -> str:
    return {
        "rate": "Rate limit. Wait a while (sometimes up to an hour) or change the proxy/IP, then try again.",
        "login": "Login required. Turn Cookies on and make sure you are signed in within that browser.",
        "rate_login": "Rate limit or login required. Sign in within the browser and turn Cookies on; "
                      "if it still fails, wait a while and try again.",
        "network": "Network error. Check your internet connection, the proxy address and port, "
                   "and that the site is reachable.",
        "ffmpeg": "FFmpeg is missing or not working.",
    }.get(classify_error(msg), "")


# ======================================
# Update helpers
# ======================================
# Pre-release stages in order: dev < alpha < beta < release candidate < final release.
_STAGE_RANK = {"dev": 0, "a": 1, "alpha": 1, "b": 2, "beta": 2, "c": 3, "rc": 3, "pre": 3, "preview": 3}
_STAGE_RE = re.compile(r"[-_.]?(preview|alpha|beta|dev|pre|rc|a|b|c)(?![a-z])[-_.]?(\d*)", re.I)
_FINAL = max(_STAGE_RANK.values()) + 1


def version_tuple(v) -> tuple:
    """Comparable version key: (numbers, stage, stage number). 1.2.3-beta2 < 1.2.3-rc1 < 1.2.3,
    trailing zeros are ignored (1.2 == 1.2.0), a leading v and +build metadata do not matter."""
    s = str(v).strip().lstrip("vV")
    m = re.match(r"(\d+(?:\.\d+)*)(.*)$", s, re.S)
    if not m:
        return ((), _FINAL, 0)
    nums = [int(n) for n in m.group(1).split(".")]
    while nums and nums[-1] == 0:
        nums.pop()
    rest = m.group(2).split("+", 1)[0]
    st = _STAGE_RE.match(rest)
    if not st:
        return (tuple(nums), _FINAL, 0)
    return (tuple(nums), _STAGE_RANK[st.group(1).lower()], int(st.group(2) or 0))


def _sidecar(exe: Path, ext: str) -> Path:
    """Temporary neighbour of the running executable (.new / .old)."""
    return exe.with_suffix(ext) if IS_WIN else exe.with_name(exe.name + ext)


def cleanup_old_exe() -> None:
    """Remove files left behind by a self-update."""
    if FROZEN:
        exe = Path(sys.executable)
        for ext in (".old", ".new"):
            try:
                _sidecar(exe, ext).unlink(missing_ok=True)
            except OSError as e:
                debug_log(f"cleanup {ext}: {e}")


def pick_release_asset(assets) -> dict | None:
    """The release file for this OS: the .exe on Windows, the *-linux binary on Linux.
    Other systems (macOS) have no build."""
    for a in assets or []:
        n = str(a.get("name", "")).lower()
        if IS_WIN and n.endswith(".exe"):
            return a
        if IS_LINUX and n.endswith("-linux"):
            return a
    return None


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def parse_sums(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            out[parts[1].lstrip("*")] = parts[0].lower()
    return out


def run_stream(cmd: list, cwd, emit) -> int:
    """Run a command and pass every output line to emit(); returns the exit code."""
    kw = {"creationflags": 0x08000000} if IS_WIN else {}  # no console window
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", bufsize=1, **kw,
    )
    for line in proc.stdout:
        emit(line.rstrip())
    return proc.wait()


def _http_ydl(proxy: str):
    opts = {"quiet": True, "no_warnings": True, "logger": _SilentLogger(), "socket_timeout": 30}
    if proxy:
        opts["proxy"] = proxy
    return yt_dlp.YoutubeDL(opts)  # reuses yt-dlp's networking: proxies (incl. socks) and CA certs


def fetch_latest_release(proxy: str) -> dict:
    if not REPO:
        raise RuntimeError("No GitHub repository is configured (set REPO in app_version.py).")
    from yt_dlp.networking import Request
    url = f"https://api.github.com/repos/{REPO}/releases/latest"
    try:
        with _http_ydl(proxy) as ydl:
            resp = ydl.urlopen(Request(url, headers={
                "Accept": "application/vnd.github+json", "User-Agent": "Retr0Downloader"}))
            try:
                return json.loads(resp.read().decode("utf-8"))
            finally:
                resp.close()
    except Exception as e:
        if "404" in str(e):
            raise RuntimeError("No release found. The repository must be public and have a release.") from e
        raise


MAX_WHEEL_DOWNLOAD = 100 * 1024 * 1024  # a package download larger than this is refused
MAX_SUMS_DOWNLOAD = 1024 * 1024  # SHA256SUMS.txt is a few lines
MAX_APP_DOWNLOAD = 600 * 1024 * 1024  # used only when the release does not report the file size


def download_asset(url: str, dest: Path, proxy: str, progress, max_bytes: int = 0) -> None:
    """Download the new program. The transfer is refused as soon as the server announces more than
    max_bytes (or MAX_APP_DOWNLOAD), or when more than that has actually arrived."""
    from yt_dlp.networking import Request
    limit = max_bytes or MAX_APP_DOWNLOAD
    with _http_ydl(proxy) as ydl:
        resp = ydl.urlopen(Request(url, headers={
            "User-Agent": "Retr0Downloader", "Accept": "application/octet-stream"}))
        try:
            total = int(resp.headers.get("Content-Length") or 0)
            if total > limit:
                raise RuntimeError("The download is larger than expected, aborted.")
            done = 0
            with dest.open("wb") as f:
                while True:
                    chunk = resp.read(262144)
                    if not chunk:
                        break
                    done += len(chunk)
                    if done > limit:
                        raise RuntimeError("The download is larger than expected, aborted.")
                    f.write(chunk)
                    progress(done, total)
        finally:
            resp.close()
    if total and done != total:
        raise RuntimeError("The download was incomplete.")
    magic, name = (b"MZ", "Windows") if IS_WIN else (b"\x7fELF", "Linux")
    with dest.open("rb") as f:
        if not f.read(4).startswith(magic):
            raise RuntimeError(f"The downloaded file is not a {name} program.")


def apply_exe_update(new_path: Path) -> None:
    """Swap the running program for the new one and start it.
    Windows allows renaming a running exe; Linux allows replacing a running binary."""
    exe = Path(sys.executable)
    env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI") and k != "_MEIPASS2"}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    try:
        if IS_WIN:
            old = _sidecar(exe, ".old")
            try:
                old.unlink(missing_ok=True)
            except OSError:
                pass
            exe.rename(old)
            try:
                new_path.replace(exe)
            except Exception:
                old.rename(exe)
                raise
            kw = {"creationflags": 0x00000008 | 0x00000200}  # DETACHED_PROCESS | NEW_PROCESS_GROUP
        else:
            os.chmod(new_path, 0o755)
            os.replace(new_path, exe)
            kw = {"start_new_session": True}
    except PermissionError as e:
        raise RuntimeError("No permission to replace the app file. Move the app to a folder you own.") from e
    subprocess.Popen([str(exe)], env=env, cwd=str(exe.parent), close_fds=True, **kw)


def restart_app() -> None:
    """Start a fresh copy of the app (the caller closes the current window)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI") and k != "_MEIPASS2"}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    cmd = [sys.executable] if FROZEN else [sys.executable, os.path.abspath(sys.argv[0])]
    kw = {"creationflags": 0x00000008 | 0x00000200} if IS_WIN else {"start_new_session": True}
    subprocess.Popen(cmd, env=env, close_fds=True, **kw)


def _pypi_json(url: str, proxy: str) -> dict:
    from yt_dlp.networking import Request
    with _http_ydl(proxy) as ydl:
        resp = ydl.urlopen(Request(url, headers={"User-Agent": "Retr0Downloader"}))
        try:
            return json.loads(resp.read().decode("utf-8"))
        finally:
            resp.close()


def _pick_wheel(files) -> dict | None:
    for f in files or []:
        if f.get("packagetype") == "bdist_wheel" and str(f.get("filename", "")).endswith("-py3-none-any.whl"):
            return f
    return None


MAX_UNPACKED = 300 * 1024 * 1024  # refuse a package that unpacks to more than this


def _stage_wheel(entry: dict, proxy: str, tmp: Path, n: int) -> tuple:
    """Download a pure-Python wheel from PyPI, verify its sha256, unpack it into tmp/stage<n> and
    validate it. Returns (stage_dir, top_level_names). Nothing in LIB_DIR is touched."""
    whl = tmp / f"{n}_{Path(entry['filename']).name}"
    size = int(entry.get("size") or 0)  # PyPI reports each file's size
    if size > MAX_WHEEL_DOWNLOAD:
        raise RuntimeError("The package is larger than expected, aborted.")
    download_asset_raw(entry["url"], whl, proxy, max_bytes=size or MAX_WHEEL_DOWNLOAD)
    want = (entry.get("digests") or {}).get("sha256")
    if not want or sha256_file(whl) != want:
        raise RuntimeError("Checksum mismatch, the download was discarded.")

    stage = tmp / f"stage{n}"
    stage.mkdir()
    root = stage.resolve()
    with zipfile.ZipFile(whl) as z:
        if z.testzip() is not None:
            raise RuntimeError("The package is corrupted, aborted.")
        if sum(i.file_size for i in z.infolist()) > MAX_UNPACKED:
            raise RuntimeError("The package unpacks to an unexpected size, aborted.")
        for info in z.infolist():
            target = (stage / info.filename).resolve()
            if target != root and root not in target.parents:
                raise RuntimeError("Unsafe path inside the package, aborted.")
            mode = info.external_attr >> 16
            if mode and stat.S_ISLNK(mode):
                raise RuntimeError("Symlink inside the package, aborted.")
        z.extractall(stage)
    names = [p.name for p in stage.iterdir() if not p.name.endswith(".data")]
    has_meta = any(n_.endswith(".dist-info") for n_ in names)
    has_code = any(
        not n_.endswith(".dist-info")
        and ((stage / n_).is_file() and n_.endswith(".py") or (stage / n_ / "__init__.py").is_file())
        for n_ in names)
    if not (has_meta and has_code):
        raise RuntimeError("The package has an unexpected layout, aborted.")
    return stage, names


def _install_wheels(entries: list, proxy: str) -> None:
    """Install one or more wheels into LIB_DIR as ONE all-or-nothing step. Every wheel is downloaded,
    verified and validated first; only then are they all swapped in under a single journal, so an
    error (or closing the app halfway) never leaves new and old packages mixed together."""
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LIB_DIR / ".tmp"
    backup = LIB_DIR / ".backup"
    journal = LIB_DIR / ".journal.json"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    try:
        # 1+2. Download, verify and unpack everything. Nothing in LIB_DIR is touched yet.
        staged = [_stage_wheel(e, proxy, tmp, i) for i, e in enumerate(entries)]
        names, stage_of = [], {}
        for stage, ns in staged:
            for n in ns:
                if n in stage_of:
                    raise RuntimeError("The packages overlap each other, aborted.")
                stage_of[n] = stage
                names.append(n)

        # 3. Work out which existing files will be replaced.
        old = []
        for n in names:
            if (LIB_DIR / n).exists() or (LIB_DIR / n).is_symlink():
                old.append(n)
            if n.endswith(".dist-info"):
                for stale in LIB_DIR.glob(n.split("-")[0] + "-*.dist-info"):
                    if stale.name not in old:
                        old.append(stale.name)

        # 4. Swap. The old files are moved to a backup folder first, and a journal lets the
        # next start roll back if the app is closed halfway. Any error restores the old version.
        shutil.rmtree(backup, ignore_errors=True)
        backup.mkdir()
        journal.write_text(json.dumps({"new": names, "old": old}), encoding="utf-8")
        try:
            for n in old:
                shutil.move(str(LIB_DIR / n), str(backup / n))
            for n in names:
                shutil.move(str(stage_of[n] / n), str(LIB_DIR / n))
            (LIB_DIR / ".for_app").write_text(VERSION)
        except BaseException:
            rolled_back = False
            try:
                _rollback_lib(names, old)
                rolled_back = True
            except Exception as rb:
                # Keep the journal: the next start (_recover_lib) tries the rollback again and, if that
                # fails too, falls back to the bundled copy. The original error is re-raised below.
                debug_log(f"rollback failed, journal kept for recovery at next start: {rb}")
            if rolled_back:
                journal.unlink(missing_ok=True)
                shutil.rmtree(backup, ignore_errors=True)
            raise

        # 5. Success: the journal goes first, so a crash here never triggers a rollback.
        journal.unlink(missing_ok=True)
        shutil.rmtree(backup, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def download_asset_raw(url: str, dest: Path, proxy: str, max_bytes: int = 0) -> None:
    """Download url to dest. With max_bytes, the transfer is refused as soon as the server announces
    more than that or more than that has arrived, and the partial file is removed."""
    from yt_dlp.networking import Request
    dest = Path(dest)
    try:
        with _http_ydl(proxy) as ydl:
            resp = ydl.urlopen(Request(url, headers={"User-Agent": "Retr0Downloader"}))
            try:
                if max_bytes and int(resp.headers.get("Content-Length") or 0) > max_bytes:
                    raise RuntimeError("The download is larger than expected, aborted.")
                done = 0
                with dest.open("wb") as f:
                    while True:
                        chunk = resp.read(1 << 18)
                        if not chunk:
                            break
                        done += len(chunk)
                        if max_bytes and done > max_bytes:
                            raise RuntimeError("The download is larger than expected, aborted.")
                        f.write(chunk)
            finally:
                resp.close()
    except BaseException:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _ejs_requirement(requires_dist):
    """Find the yt-dlp-ejs requirement in yt-dlp's PyPI metadata (its Requires-Dist entries).
    Returns the exact pinned version, or None if yt-dlp does not require yt-dlp-ejs at all.
    Raises if it is required in any form that is not one exact pin (>=, ~=, ==0.3.*, a URL...):
    this updater cannot install that safely, and a new yt-dlp without the matching yt-dlp-ejs
    can break YouTube, so the update must stop instead of continuing without it."""
    for req in requires_dist or []:
        spec = str(req).split(";", 1)[0].strip()  # what follows ';' is an environment marker
        m = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(.*)$", spec)
        if not m or re.sub(r"[-_.]+", "-", m.group(1)).lower() != "yt-dlp-ejs":
            continue
        rest = m.group(2).strip().strip("()").strip()
        pin = re.fullmatch(r"==\s*([A-Za-z0-9][A-Za-z0-9._]*)", rest)
        if pin:
            return pin.group(1)
        raise RuntimeError(f"yt-dlp requires yt-dlp-ejs ({rest or 'any version'}), which cannot be pinned "
                           "to one exact version here. yt-dlp was not updated. Update the app instead.")
    return None


def update_ytdlp_lib(proxy: str, log) -> bool:
    """Packaged app: fetch the latest yt-dlp together with its pinned yt-dlp-ejs from PyPI into LIB_DIR.
    Both are installed in one step: either both are updated or nothing changes.
    Returns True if something was installed (a restart is then needed)."""
    meta = _pypi_json("https://pypi.org/pypi/yt-dlp/json", proxy)
    latest = meta["info"]["version"]
    current = getattr(yt_dlp.version, "__version__", "unknown")
    if latest == current:
        log(f"yt-dlp is already up to date ({current}).", "ok")
        return False
    need = re.match(r">=\s*(\d+)\.(\d+)", meta["info"].get("requires_python") or "")
    if need and sys.version_info[:2] < (int(need[1]), int(need[2])):
        raise RuntimeError(f"yt-dlp {latest} needs Python {need[1]}.{need[2]} or newer. "
                           "Update the app instead.")
    wheel = _pick_wheel(meta.get("urls"))
    if not wheel:
        raise RuntimeError("No installable package found for the latest yt-dlp.")
    wheels = [wheel]
    ejs_version = _ejs_requirement(meta["info"].get("requires_dist")) or ""
    if ejs_version:
        ejs = _pick_wheel(_pypi_json(f"https://pypi.org/pypi/yt-dlp-ejs/{ejs_version}/json", proxy).get("urls"))
        if not ejs:
            # yt-dlp needs exactly this yt-dlp-ejs; a new yt-dlp with an old one can break YouTube.
            raise RuntimeError(f"No installable package found for yt-dlp-ejs {ejs_version}. "
                               "Nothing was changed.")
        wheels.append(ejs)
    log(f"Updating yt-dlp {current} -> {latest}"
        + (f" and yt-dlp-ejs {ejs_version}" if ejs_version else "") + " ...", "info")
    _install_wheels(wheels, proxy)
    log(f"yt-dlp updated to {latest}.", "ok")
    return True


def linux_pick_folder(start: str):
    """Native Linux folder dialog. Path if chosen, '' if cancelled, None if no helper is installed."""
    if shutil.which("zenity"):
        cmd = ["zenity", "--file-selection", "--directory", "--title=Select download folder",
               f"--filename={start.rstrip('/')}/"]
    elif shutil.which("kdialog"):
        cmd = ["kdialog", "--getexistingdirectory", start]
    else:
        return None
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=host_env())
    except Exception as e:
        debug_log(f"linux_pick_folder: {e}")
        return None
    if r.returncode == 0:
        return r.stdout.strip()
    return "" if r.returncode == 1 else None  # 1 = cancelled; anything else = the helper crashed


# ======================================
# Playlist helpers
# ======================================
def is_youtube_playlist(url: str) -> bool:
    """True for playlist URLs, including watch?v=..&list=.. links.
    Auto-generated mixes (list=RD...) are endless, so they stay single videos."""
    try:
        u = urlparse(url.strip())
    except Exception:
        return False
    host = (u.hostname or "").lower()
    if not (host.endswith("youtube.com") or host == "youtu.be" or host.endswith("youtube-nocookie.com")):
        return False
    lst = parse_qs(u.query).get("list", [""])[0]
    if not lst or lst.startswith(("RD", "UL")):
        return False
    return True


def fetch_playlist_links(url: str, proxy: str, cookies, fallback_cookies=None) -> tuple:
    """Return (title, [video urls]) using a flat extraction (nothing is downloaded)."""
    lst = parse_qs(urlparse(url).query).get("list", [""])[0]
    canonical = f"https://www.youtube.com/playlist?list={lst}"

    def attempt(ck):
        opts = {
            "extract_flat": "in_playlist",
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": False,
            "socket_timeout": 60,
            "retries": 10,
            "logger": _SilentLogger(),
        }
        if proxy:
            opts["proxy"] = proxy
        if ck:
            opts["cookiesfrombrowser"] = ck
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(canonical, download=False)

    tried = set()
    sources = []
    if cookies:
        sources.append(tuple(cookies))
    sources.extend(tuple(c) for c in (fallback_cookies or []))
    sources.append(None)
    info = None
    last_error = None
    for ck in sources:
        marker = tuple(ck) if ck else None
        if marker in tried:
            continue
        tried.add(marker)
        try:
            info = attempt(ck)
            break
        except Exception as exc:
            last_error = exc
            message = clean(exc).lower()
            auth_or_cookie_error = any(term in message for term in (
                "sign in", "login required", "authentication", "not a bot", "cookie",
                "cookies", "database is locked", "could not copy",
            ))
            # Only try another browser profile for likely authentication/cookie errors.
            if not auth_or_cookie_error:
                raise
            if ck:
                continue  # a later profile may contain the valid login
            raise
    if info is None:
        raise last_error or RuntimeError("Could not read playlist information")

    links = []
    for e in info.get("entries") or []:
        if not e:
            continue
        link = e.get("url") or e.get("webpage_url")
        if (not link or not link.startswith("http")) and e.get("id"):
            link = f"https://www.youtube.com/watch?v={e['id']}"
        if link:
            links.append(link)
    return info.get("title") or "playlist", links


def read_url_file(path: Path) -> list:
    with path.open("r", encoding="utf-8-sig") as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]


# ======================================
# Core download function
# ======================================
class _SilentLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def download_video(url, out_dir, proxy, cookies, progress_callback=None,
                   cancel_event=None, impersonate=True, retries=20, batch=False,
                   audio_only=False):
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    rand = uuid.uuid4().hex[:8]

    if "instagram.com/reels/" in url.lower():
        url = url.replace("/reels/", "/reel/")

    headers = {"User-Agent": user_agent(), "Accept-Language": "en-US,en;q=0.9"}
    low = url.lower()
    if "pornhub.com" in low:
        headers["Referer"] = url
    elif "instagram.com" in low:
        headers["Referer"] = "https://www.instagram.com/"
        headers["X-IG-App-ID"] = "936619743392459"

    opts = {
        "format": "bestvideo*+bestaudio/best",
        # Resolution and fps first; prefer widely playable VP9 over AV1, then bitrate.
        "format_sort": ["res", "fps", "vcodec:vp9.2", "vcodec:vp9", "br"],
        "merge_output_format": "mkv",
        "outtmpl": str(out_dir / f"{timestamp}_{rand}_%(title).150B.%(ext)s"),
        "noplaylist": True,
        "windowsfilenames": IS_WIN,
        "extractor_args": {
            "youtube": {
                "player_client": ["tv", "web_embedded", "android_vr", "web", "android"],
                "player_skip": ["webpage"],
            },
            "instagram": {"app_id": ["web"]},
        },
        "retries": retries,
        "fragment_retries": retries,
        "file_access_retries": 10,
        "continuedl": True,
        "ignoreerrors": False,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "consoletitle": False,
        "logger": _SilentLogger(),
        "http_headers": headers,
        "concurrent_fragment_downloads": 3,
        "socket_timeout": 60,
    }
    if batch:
        # Pause between videos only when downloading many, to avoid rate limits.
        opts["sleep_interval"] = 1
        opts["max_sleep_interval"] = 3

    if audio_only:
        # Best audio stream, converted to MP3 at 320 kbps (the maximum for MP3).
        opts["format"] = "bestaudio/best"
        opts["format_sort"] = ["abr", "asr"]
        opts.pop("merge_output_format", None)
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "320",
        }]

    deno = find_deno()
    if deno:
        opts["js_runtimes"] = {"deno": {"path": deno}}
        try:
            import yt_dlp_ejs  # noqa: F401
        except Exception:
            opts["remote_components"] = {"ejs": "github"}

    ff = find_ffmpeg()
    if ff:
        opts["ffmpeg_location"] = ff
    if cookies:
        opts["cookiesfrombrowser"] = cookies
        # The forced client list (tv, android_vr, android) ignores cookies or needs PO tokens, and
        # skipping the webpage hides the login state. With cookies, let yt-dlp choose its own clients.
        opts["extractor_args"].pop("youtube", None)
    if proxy:
        opts["proxy"] = proxy

    if impersonate:
        try:
            from yt_dlp.networking.impersonate import ImpersonateTarget
            opts["impersonate"] = ImpersonateTarget.from_str("chrome")
        except Exception as e:
            debug_log(f"impersonate unavailable: {e}")

    finished_streams = [0]

    def hook(d):
        if cancel_event is not None and cancel_event.is_set():
            raise yt_dlp.utils.DownloadCancelled("Cancelled by user")
        if not progress_callback:
            return
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            progress_callback(d.get("downloaded_bytes", 0), total,
                              clean(d.get("_speed_str") or "").strip(), d.get("eta"))
        elif d.get("status") == "finished":
            # Video and audio are separate streams: only report "finished" (and start
            # "Merging...") once the last of them has been downloaded.
            expected = len((d.get("info_dict") or {}).get("requested_formats") or []) or 1
            finished_streams[0] += 1
            if finished_streams[0] >= expected:
                progress_callback(None, None, None, None, finished=True)

    opts["progress_hooks"] = [hook]
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


# ======================================
# Widgets
# ======================================
# Physical keys for C, V, X, A. Tk matches Ctrl+<letter> by the typed letter, so on a
# non-Latin layout (Persian, Russian...) the default shortcuts never fire.
CTRL_KEYS = {67: "c", 86: "v", 88: "x", 65: "a"} if IS_WIN else {54: "c", 55: "v", 53: "x", 38: "a"}


def ctrl_shortcut(e):
    if IS_MAC or e.keysym.lower() in ("c", "v", "x", "a"):
        return None  # Latin layout: Tk's own binding handles it
    act = CTRL_KEYS.get(e.keycode)
    if not act:
        return None
    w = e.widget
    if act == "a":
        if isinstance(w, (tk.Entry, ttk.Entry)):
            w.select_range(0, "end")
            w.icursor("end")
        else:
            w.tag_add("sel", "1.0", "end")
    else:
        w.event_generate({"c": "<<Copy>>", "v": "<<Paste>>", "x": "<<Cut>>"}[act])
    return "break"


class Btn(tk.Label):
    """Flat label-based button: full colour control on every OS."""

    def __init__(self, master, text, command, fg, bg=PANEL, border=None,
                 hover_bg=None, hover_fg=BG, font=None):
        border = border or fg
        super().__init__(
            master, text=text, fg=fg, bg=bg, font=font, cursor="hand2",
            padx=px(16), pady=px(7), highlightthickness=1,
            highlightbackground=border, highlightcolor=border,
        )
        self._cmd = command
        self._fg, self._bg, self._border = fg, bg, border
        self._hbg, self._hfg = hover_bg or border, hover_fg
        self._enabled = True
        self.bind("<Enter>", lambda e: self._paint(True))
        self.bind("<Leave>", lambda e: self._paint(False))
        self.bind("<ButtonRelease-1>", self._release)

    def _paint(self, hover):
        if not self._enabled:
            self.configure(fg=DIM, bg=PANEL, highlightbackground=LINE, highlightcolor=LINE, cursor="arrow")
        elif hover:
            self.configure(fg=self._hfg, bg=self._hbg)
        else:
            self.configure(fg=self._fg, bg=self._bg, highlightbackground=self._border,
                           highlightcolor=self._border, cursor="hand2")

    def _release(self, e):
        if self._enabled and 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height():
            self._cmd()

    def set_enabled(self, on):
        self._enabled = on
        self._paint(False)


class Toggle(tk.Canvas):
    def __init__(self, master, value=False, bg=BG):
        self.w, self.h = px(46), px(24)
        super().__init__(master, width=self.w, height=self.h, bg=bg,
                         highlightthickness=0, cursor="hand2")
        self.value = bool(value)
        self.bind("<Button-1>", lambda e: self.set(not self.value))
        self._draw()

    def get(self):
        return self.value

    def set(self, v):
        self.value = bool(v)
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h, m = self.w, self.h, px(3)
        on = self.value
        track = "#003d26" if on else PANEL
        edge = GREEN if on else LINE
        self.create_oval(1, 1, h - 1, h - 1, fill=track, outline=edge)
        self.create_oval(w - h + 1, 1, w - 1, h - 1, fill=track, outline=edge)
        self.create_rectangle(h // 2, 1, w - h // 2, h - 1, fill=track, outline=track)
        self.create_line(h // 2, 1, w - h // 2, 1, fill=edge)
        self.create_line(h // 2, h - 1, w - h // 2, h - 1, fill=edge)
        k = h - 2 * m
        x = w - m - k if on else m
        self.create_oval(x, m, x + k, m + k, fill=GREEN if on else "#475569", outline="")


class Bar(tk.Canvas):
    def __init__(self, master):
        super().__init__(master, height=px(8), bg=LINE, highlightthickness=0)
        self.pct = 0
        self.bind("<Configure>", lambda e: self._draw())

    def set(self, pct):
        self.pct = max(0, min(100, pct))
        self._draw()

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        if self.pct > 0:
            self.create_rectangle(0, 0, int(w * self.pct / 100), self.winfo_height(), fill=GREEN, outline="")


# ======================================
# Splash screen (pixel-art banner, drawn with rectangles so it needs no special font)
# ======================================
GLYPHS = {
    "R": ["####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"],
    "E": ["#####", "#....", "#....", "####.", "#....", "#....", "#####"],
    "T": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."],
    "0": [".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."],
}
WORD = "RETR0"


class Splash(tk.Canvas):
    def __init__(self, app, boot_lines):
        super().__init__(app.root, bg=BG2, highlightthickness=0)
        self.app = app
        self.lines = boot_lines
        self.frame = 0
        self.finished = False
        self.place(x=0, y=0, relwidth=1, relheight=1)
        self.tk.call("raise", self._w)  # Canvas.lift() means tag_raise, so raise the widget directly
        self.focus_set()
        self.bind("<Button-1>", self.finish)
        self.bind("<Key>", self.finish)
        self.after(50, self.tick)

    def tick(self):
        if self.finished:
            return
        self.draw()
        self.frame += 1
        if self.frame >= 80:
            self.finish()
        else:
            self.after(50, self.tick)

    def draw(self):
        self.delete("all")
        w = max(self.winfo_width(), 200)
        h = max(self.winfo_height(), 200)
        cols = len(WORD) * 5 + (len(WORD) - 1)
        cell = max(4, min(int(w * 0.66 / cols), px(16)))
        block_h = 7 * cell + px(210)
        y0 = max(px(16), (h - block_h) // 2)
        x0 = (w - cols * cell) // 2
        glitch = self.frame < 26

        for r in range(7):
            color = GRADIENT[r]
            dx = 0
            if glitch and random.random() < 0.35:
                dx = random.randint(-3, 3) * max(1, cell // 2)
                color = random.choice([PINK, CYAN, "#ffffff"])
            x = x0
            for ch in WORD:
                row = GLYPHS[ch][r]
                for c, bit in enumerate(row):
                    if bit == "#":
                        if glitch and random.random() < 0.03:
                            continue
                        px0 = x + c * cell + dx
                        py0 = y0 + r * cell
                        self.create_rectangle(px0, py0, px0 + cell - 1, py0 + cell - 1,
                                              fill=color, outline="")
                x += 6 * cell
        if glitch:
            for _ in range(8):
                nx = x0 + random.randint(-cell * 3, cols * cell + cell * 3)
                ny = y0 + random.randint(-cell, 8 * cell)
                self.create_rectangle(nx, ny, nx + random.randint(cell, cell * 3), ny + max(2, cell // 4),
                                      fill=random.choice([PINK, CYAN, GREEN]), outline="")

        ty = y0 + 7 * cell + px(34)

        shown = max(0, (self.frame - 12) // 5)
        left = w // 2 - px(190)
        for i, (ok, msg) in enumerate(self.lines[:shown]):
            yy = ty + px(40) + i * px(22)
            self.create_text(left, yy, text="[+]" if ok else "[!]", fill=GREEN if ok else PINK,
                             anchor="w", font=self.app.f_bold)
            self.create_text(left + px(40), yy, text=msg, fill="#94a3b8" if ok else PINK,
                             anchor="w", font=self.app.f_mono)
        if self.frame > 44:
            self.create_text(w // 2, ty + px(40) + len(self.lines) * px(22) + px(26),
                             text="Created By Retr0", fill=CYAN, font=self.app.f_title)
        self.create_text(w // 2, h - px(20), text="click or press any key to skip", fill="#475569",
                         font=self.app.f_mono)

    def finish(self, _e=None):
        if self.finished:
            return
        self.finished = True
        self.destroy()
        self.app.url_entry.focus_set()


# ======================================
# Application
# ======================================
class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_settings()
        self.q = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.updating = False
        self.pending_restart = False  # yt-dlp was updated but the app has not been restarted yet

        root.title(APP_NAME)
        set_window_icon(root)
        root.configure(bg=BG)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = min(px(940), int(sw * 0.95)), min(px(780), int(sh * 0.92))
        root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")
        root.minsize(min(px(760), w), min(px(640), h))
        root.report_callback_exception = self.on_exception

        self.setup_fonts()
        self.setup_styles()
        self.build_ui()
        self.write("Created By Retr0", "pink")
        self.write(f"Output folder: {self.var_out.get()}", "dim")
        if not find_ffmpeg():
            self.write("FFmpeg not found. Highest quality needs it to merge video and audio.", "err")
        if not find_deno():
            self.write("Deno not found. Some sites, YouTube especially, may fail without it.", "warn")

        ytv = getattr(getattr(yt_dlp, "version", None), "__version__", "?")
        self.write(f"App version: {VERSION}    yt-dlp: {ytv}", "dim")

        Splash(self, self.boot_lines())
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(40, self.poll)
        root.after(6000, self.auto_check)

    # ---------- setup ----------
    def setup_fonts(self):
        fams = set(tkfont.families())
        mono = next((f for f in ("Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono",
                                 "Liberation Mono", "Courier New") if f in fams), "TkFixedFont")
        self.f_mono = tkfont.Font(family=mono, size=10)
        self.f_bold = tkfont.Font(family=mono, size=10, weight="bold")
        self.f_title = tkfont.Font(family=mono, size=12, weight="bold")

    def setup_styles(self):
        st = ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("Neon.TCombobox", fieldbackground=PANEL, background=PANEL, foreground=TEXT,
                     bordercolor=LINE, lightcolor=PANEL, darkcolor=PANEL, arrowcolor=CYAN,
                     arrowsize=px(14), padding=px(5))
        st.map("Neon.TCombobox",
               fieldbackground=[("readonly", PANEL)], foreground=[("readonly", TEXT)],
               selectbackground=[("readonly", PANEL)], selectforeground=[("readonly", TEXT)],
               bordercolor=[("focus", GREEN)])
        st.configure("Neon.Vertical.TScrollbar", background=LINE, troughcolor=BG2, bordercolor=BG2,
                     arrowcolor=CYAN, lightcolor=LINE, darkcolor=LINE)
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", GREEN)
        self.root.option_add("*TCombobox*Listbox.selectForeground", BG)
        self.root.option_add("*TCombobox*Listbox.font", self.f_mono)

    def entry(self, parent, var):
        return tk.Entry(
            parent, textvariable=var, bg=PANEL, fg=TEXT, insertbackground=GREEN,
            relief="flat", highlightthickness=1, highlightbackground=LINE, highlightcolor=GREEN,
            selectbackground="#164e3a", selectforeground=TEXT, font=self.f_mono,
        )

    def label(self, parent, text, fg=CYAN):
        return tk.Label(parent, text=text, fg=fg, bg=BG, font=self.f_bold, anchor="w")

    def build_ui(self):
        pad = px(24)
        top = tk.Frame(self.root, bg=PANEL)
        top.pack(fill="x")
        tk.Label(top, text="RETR0 // Highest-Quality Video Downloader", fg=GREEN, bg=PANEL,
                 font=self.f_title).pack(side="left", padx=pad, pady=px(14))
        tk.Label(top, text=f"Created By Retr0   {VERSION}", fg=PINK, bg=PANEL,
                 font=self.f_bold).pack(side="right", padx=pad)
        tk.Frame(self.root, bg=GREEN, height=2).pack(fill="x")

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=pad, pady=px(16))
        body.columnconfigure(1, weight=1)
        body.rowconfigure(8, weight=1)
        ipad = px(7)
        rp = px(6)

        # URL
        self.var_url = tk.StringVar()
        self.label(body, "URL / .txt").grid(row=0, column=0, sticky="w", pady=rp, padx=(0, px(14)))
        self.url_entry = self.entry(body, self.var_url)
        self.url_entry.grid(row=0, column=1, sticky="ew", ipady=ipad, pady=rp)
        self.url_entry.bind("<Return>", lambda e: self.start_download())
        Btn(body, "Paste", self.paste, CYAN, font=self.f_bold).grid(
            row=0, column=2, sticky="ew", padx=(px(10), 0), pady=rp)
        Btn(body, "Choose .txt", self.choose_url_file, CYAN, font=self.f_bold).grid(
            row=0, column=3, sticky="ew", padx=(px(10), 0), pady=rp)
        tk.Label(body, text="Paste a video/playlist link, or choose a .txt file containing one link per line",
                 fg=DIM, bg=BG, font=self.f_mono, anchor="w").grid(row=1, column=1, columnspan=3, sticky="w")

        # Save path
        self.var_out = tk.StringVar(value=self.cfg["out_dir"])
        self.label(body, "Save to").grid(row=2, column=0, sticky="w", pady=rp, padx=(0, px(14)))
        self.entry(body, self.var_out).grid(row=2, column=1, sticky="ew", ipady=ipad, pady=rp)
        Btn(body, "Browse", self.browse, CYAN, font=self.f_bold).grid(
            row=2, column=2, sticky="ew", padx=(px(10), 0), pady=rp)
        Btn(body, "Open", self.open_out, CYAN, font=self.f_bold).grid(
            row=2, column=3, sticky="ew", padx=(px(10), 0), pady=rp)

        # Proxy
        self.var_proxy = tk.StringVar(value=self.cfg["proxy"])
        self.label(body, "Proxy").grid(row=3, column=0, sticky="w", pady=rp, padx=(0, px(14)))
        pf = tk.Frame(body, bg=BG)
        pf.grid(row=3, column=1, columnspan=3, sticky="ew", pady=rp)
        pf.columnconfigure(1, weight=1)
        self.sw_proxy = Toggle(pf, self.cfg["use_proxy"])
        self.sw_proxy.grid(row=0, column=0, padx=(0, px(12)))
        self.entry(pf, self.var_proxy).grid(row=0, column=1, sticky="ew", ipady=ipad)
        tk.Label(pf, text="host:port or socks5://host:port", fg=DIM, bg=BG,
                 font=self.f_mono).grid(row=0, column=2, padx=(px(10), 0))

        # Cookies
        self.var_browser = tk.StringVar(value=self.cfg["browser"])
        self.var_profile = tk.StringVar(value=self.cfg["profile"])
        self.label(body, "Cookies").grid(row=4, column=0, sticky="w", pady=rp, padx=(0, px(14)))
        cf = tk.Frame(body, bg=BG)
        cf.grid(row=4, column=1, columnspan=3, sticky="ew", pady=rp)
        cf.columnconfigure(2, weight=1)
        self.sw_cookies = Toggle(cf, self.cfg["use_cookies"])
        self.sw_cookies.grid(row=0, column=0, padx=(0, px(12)))
        self.cb_browser = ttk.Combobox(cf, textvariable=self.var_browser, values=BROWSERS,
                                       state="readonly", width=10, style="Neon.TCombobox",
                                       font=self.f_mono)
        self.cb_browser.grid(row=0, column=1, padx=(0, px(10)), ipady=px(2))
        self.cb_browser.bind("<<ComboboxSelected>>", self.on_browser)
        self.entry(cf, self.var_profile).grid(row=0, column=2, sticky="ew", ipady=ipad)
        tk.Label(cf, text="profile folder (optional)", fg=DIM, bg=BG,
                 font=self.f_mono).grid(row=0, column=3, padx=(px(10), 0))
        tk.Label(cf, text="Only load cookies if a website requires login to download (common on YouTube); use a browser where you're already signed in. Firefox is recommended for YouTube.",
                 fg=DIM, bg=BG, font=self.f_mono, justify="left", wraplength=px(600),
                 anchor="w").grid(row=1, column=0, columnspan=4, sticky="w", pady=(px(6), 0))
        # Opt-in: by default only the selected browser's profiles are tried when a sign-in is needed.
        self.sw_all_browsers = Toggle(cf, self.cfg.get("cookie_all_browsers", False))
        self.sw_all_browsers.grid(row=2, column=0, padx=(0, px(12)), pady=(px(8), 0))
        tk.Label(cf, text="If sign-in fails, automatically try cookies from other installed browsers",
                 fg=DIM, bg=BG, font=self.f_mono, justify="left", wraplength=px(600),
                 anchor="w").grid(row=2, column=1, columnspan=3, sticky="w", pady=(px(8), 0))

        # Action buttons
        bf = tk.Frame(body, bg=BG)
        bf.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(px(14), px(8)))
        left = tk.Frame(bf, bg=BG)
        left.pack(side="left")
        right = tk.Frame(bf, bg=BG)
        right.pack(side="right")
        self.btn_go = Btn(left, "DOWNLOAD", self.start_download, BG, bg=GREEN, border=GREEN,
                          hover_bg=GREEN_HI, hover_fg=BG, font=self.f_bold)
        self.btn_go.pack(side="left", padx=(0, px(10)))
        self.btn_stop = Btn(left, "Stop", self.stop, PINK, bg="#1a0a12", hover_fg=BG, font=self.f_bold)
        self.btn_stop.pack(side="left", padx=(0, px(10)))
        self.btn_stop.set_enabled(False)
        Btn(left, "Clear", self.clear_log, CYAN, font=self.f_bold).pack(side="left")
        self.sw_audio = Toggle(right, self.cfg.get("audio_only", False))
        self.sw_audio.pack(side="left", padx=(0, px(10)))
        tk.Label(right, text="Audio only (MP3)", fg=CYAN, bg=BG, font=self.f_bold).pack(
            side="left", padx=(0, px(24)))
        self.btn_upd_app = Btn(right, "Update", self.update_all, CYAN, font=self.f_bold)
        self.btn_upd_app.pack(side="left")

        # Status and progress
        self.var_status = tk.StringVar(value="Ready")
        tk.Label(body, textvariable=self.var_status, fg=CYAN, bg=BG, font=self.f_mono,
                 anchor="w").grid(row=6, column=0, columnspan=4, sticky="ew")
        self.bar = Bar(body)
        self.bar.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(px(4), px(12)))

        # Log
        lf = tk.Frame(body, bg=BG2, highlightthickness=1, highlightbackground=PINK,
                      highlightcolor=PINK)
        lf.grid(row=8, column=0, columnspan=4, sticky="nsew")
        lf.rowconfigure(0, weight=1)
        lf.columnconfigure(0, weight=1)
        self.log = tk.Text(lf, bg=BG2, fg="#94a3b8", relief="flat", wrap="word", font=self.f_mono,
                           padx=px(10), pady=px(8), state="disabled", highlightthickness=0,
                           cursor="arrow", selectbackground="#164e3a")
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.log.yview,
                           style="Neon.Vertical.TScrollbar")
        sb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=sb.set)
        for tag, color in (("ok", GREEN), ("err", PINK), ("warn", YELLOW), ("info", CYAN),
                           ("dim", DIM), ("pink", PINK), ("text", "#cbd5e1")):
            self.log.tag_configure(tag, foreground=color)
        self.log.tag_configure("head", foreground=CYAN, font=self.f_bold)

    def boot_lines(self):
        ff, dn = find_ffmpeg(), find_deno()
        return [
            (True, f"tunnel layer ...... {platform.system().lower()}"),
            (bool(ff), "ffmpeg ............ " + ("online" if ff else "MISSING")),
            (bool(dn), "js runtime ........ " + ("online" if dn else "MISSING")),
            (True, "proxy route ....... " + (redact_proxy(self.cfg["proxy"]) if self.cfg["use_proxy"] else "direct")),
            (True, "cookie jar ........ " + (self.cfg["browser"] if self.cfg["use_cookies"] else "off")),
            (True, "handshake complete"),
        ]

    # ---------- small actions ----------
    def on_browser(self, _e=None):
        opera_profile = default_profile()
        b = self.var_browser.get()
        cur = self.var_profile.get().strip()
        if b != "opera" and cur and cur == opera_profile:
            self.var_profile.set("")
        elif b == "opera" and not cur:
            self.var_profile.set(opera_profile)

    def paste(self):
        try:
            txt = self.root.clipboard_get()
        except tk.TclError:
            txt = ""
        txt = txt.strip()
        if not txt:
            self.var_status.set("Clipboard is empty")
            return
        first = txt.splitlines()[0].strip().strip('"').strip("'")
        self.var_url.set(first)
        self.url_entry.focus_set()
        self.url_entry.icursor("end")

    def choose_url_file(self):
        """Choose a text file containing video links, without typing its path."""
        chosen = filedialog.askopenfilename(
            parent=self.root,
            title="Select a text file containing video links",
            initialdir=str(Path.home()),
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if chosen:
            self.var_url.set(chosen)
            self.var_status.set(f"Selected link list: {Path(chosen).name}")
            self.url_entry.icursor("end")

    def out_path(self) -> Path:
        raw = self.var_out.get().strip()
        return Path(os.path.expandvars(raw)).expanduser() if raw else default_out_dir()

    def browse(self):
        cur = self.out_path()
        start = str(cur) if cur.exists() else str(Path.home())
        chosen = None if IS_WIN or IS_MAC else linux_pick_folder(start)
        if chosen is None:
            chosen = filedialog.askdirectory(parent=self.root, initialdir=start,
                                             title="Select download folder")
        if chosen:
            self.var_out.set(os.path.normpath(chosen))

    def open_out(self):
        if not open_folder(self.out_path()):
            messagebox.showerror(APP_NAME, "Could not open the folder.")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        if not self.busy:
            self.var_status.set("Ready")
            self.bar.set(0)

    def stop(self):
        if self.busy:
            self.cancel.set()
            self.var_status.set("Stopping...")

    def on_close(self):
        self.cancel.set()
        self.root.destroy()

    def on_exception(self, exc, val, tb):
        text = "".join(traceback.format_exception(exc, val, tb))
        try:
            d = app_dir()
            d.mkdir(parents=True, exist_ok=True)
            with (d / "crash.log").open("a", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat()}\n{redact(text)}\n")
        except Exception:
            pass
        self.write(f"Internal error: {val}", "err")

    # ---------- log and queue ----------
    def write(self, text, tag="text"):
        text = redact(text)
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.configure(state="disabled")
        self.log.see("end")

    def set_busy(self, b):
        self.busy = b
        self.btn_go.set_enabled(not b)
        self.btn_stop.set_enabled(b)

    def poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                kind = item[0]
                if kind == "log":
                    self.write(item[1], item[2])
                elif kind == "status":
                    self.var_status.set(item[1])
                elif kind == "progress":
                    self.bar.set(item[1])
                elif kind == "busy":
                    self.set_busy(item[1])
                elif kind == "update_done":
                    self.updating = False
                    self.set_update_buttons(True)
                elif kind == "ask_update":
                    _, tag, url, size, aname, sums_url = item
                    mb = f" (about {size / 1048576:.0f} MB)" if size else ""
                    if messagebox.askyesno(
                            APP_NAME, f"Version {tag} is available. You have {VERSION}.\n\n"
                                      f"Download{mb} and install it now? The app restarts when done."):
                        threading.Thread(target=self.download_update_worker,
                                         args=(url, tag, self.current_proxy(), aname, sums_url, size),
                                         daemon=True).start()
                    else:
                        self.updating = False
                        self.set_update_buttons(True)
                        self.var_status.set("Ready")
                        if self.pending_restart:
                            # yt-dlp was updated earlier in this run and the user skipped the app update.
                            self.q.put(("ask_restart", "yt-dlp was updated."))
                elif kind == "ask_restart":
                    self.pending_restart = False
                    if messagebox.askyesno(APP_NAME, item[1] + "\n\nRestart the app now to use it?"):
                        try:
                            restart_app()
                            self.root.destroy()
                            return
                        except Exception as e:
                            debug_log(f"restart_app: {e}")
                            messagebox.showinfo(APP_NAME, "Please close and reopen the app.")
                elif kind == "quit":
                    self.root.destroy()
                    return
        except queue.Empty:
            pass
        self.root.after(40, self.poll)

    # ---------- updates ----------
    def current_proxy(self) -> str:
        if self.sw_proxy.get() and self.var_proxy.get().strip():
            return normalize_proxy(self.var_proxy.get())
        return ""

    def set_update_buttons(self, on):
        self.btn_upd_app.set_enabled(on)

    def _begin_update(self, target, *args) -> bool:
        if self.busy or self.updating:
            self.var_status.set("Wait for the current task to finish")
            return False
        self.updating = True
        self.set_update_buttons(False)
        threading.Thread(target=target, args=args, daemon=True).start()
        return True

    def update_all(self):
        """One button: update the packages, then check for a newer app."""
        if FROZEN:
            self._begin_update(self.update_ytdlp_worker, self.current_proxy())
        else:
            self._begin_update(self.update_all_source_worker, self.current_proxy())

    def update_all_source_worker(self, proxy):
        self.update_packages_worker(proxy, finish=False)
        self.update_source_worker(finish=False)
        self.q.put(("update_done",))

    def update_ytdlp_worker(self, proxy):
        q = self.q
        changed = False
        try:
            q.put(("status", "Checking packages..."))
            changed = update_ytdlp_lib(proxy, lambda m, t="text": q.put(("log", m, t)))
        except Exception as e:
            q.put(("log", f"Update failed: {clean(e)}", "err"))
        if not CAN_SELF_UPDATE:
            q.put(("log", "Self-update of the app is not available on this system.", "dim"))
            if changed:
                q.put(("ask_restart", "yt-dlp was updated."))
            q.put(("status", "Ready"))
            q.put(("update_done",))
            return
        q.put(("log", "Checking for a newer app (ffmpeg, Deno and curl_cffi come with it)...", "dim"))
        # If an app update is offered and declined (or fails), the restart prompt for the
        # yt-dlp update is shown from there; see pending_restart in poll() and the update worker.
        self.pending_restart = changed
        offered = self.update_check_worker(proxy, False)  # sends update_done itself unless it offers an update
        if not offered:
            self.pending_restart = False
            if changed:
                q.put(("ask_restart", "yt-dlp was updated."))
            q.put(("status", "Ready"))

    def auto_check(self):
        if FROZEN and CAN_SELF_UPDATE and REPO and not self.busy and not self.updating:
            self._begin_update(self.update_check_worker, self.current_proxy(), True)

    def update_check_worker(self, proxy, silent) -> bool:
        """Returns True only if an update was offered to the user (update_done is sent later)."""
        q = self.q
        try:
            rel = fetch_latest_release(proxy)
            tag = str(rel.get("tag_name", ""))
            if version_tuple(tag) <= version_tuple(VERSION):
                if not silent:
                    q.put(("log", f"You are up to date (version {VERSION}).", "ok"))
                q.put(("update_done",))
                return False
            asset = pick_release_asset(rel.get("assets", []))
            if not asset:
                q.put(("log", f"Version {tag} exists but has no build for your system.", "warn"))
                q.put(("update_done",))
                return False
            sums = next((a for a in rel.get("assets", []) if a.get("name") == "SHA256SUMS.txt"), None)
            q.put(("ask_update", tag, asset["browser_download_url"], int(asset.get("size") or 0),
                   asset["name"], sums["browser_download_url"] if sums else ""))
            return True
        except Exception as e:
            if not silent:
                q.put(("log", f"Update check failed: {clean(e)}", "err"))
            else:
                debug_log(f"auto update check: {clean(e)}")
            q.put(("update_done",))
            return False

    def download_update_worker(self, url, tag, proxy, aname="", sums_url="", size=0):
        q = self.q
        exe = Path(sys.executable)
        new = _sidecar(exe, ".new")

        def prog(done, total):
            if total:
                q.put(("progress", int(done / total * 100)))
                q.put(("status", f"Downloading update  {done / 1048576:.1f} / {total / 1048576:.1f} MB"))
            else:
                q.put(("status", f"Downloading update  {done / 1048576:.1f} MB"))

        try:
            q.put(("log", f"Downloading version {tag}...", "info"))
            download_asset(url, new, proxy, prog, max_bytes=(size + 65536) if size else 0)
            # Note: SHA256SUMS.txt comes from the same GitHub release as the program, so this check
            # detects a corrupted or truncated download, not a tampered release. Say so in the README.
            if not sums_url:
                raise RuntimeError("This release has no SHA256SUMS.txt. Update aborted.")
            sums_file = new.with_name(new.name + ".sums")
            try:
                download_asset_raw(sums_url, sums_file, proxy, max_bytes=MAX_SUMS_DOWNLOAD)
                want = parse_sums(sums_file.read_text(encoding="utf-8", errors="replace")).get(aname)
            finally:
                sums_file.unlink(missing_ok=True)
            if not want:
                raise RuntimeError(f"Checksum for {aname} was not found. Update aborted.")
            if sha256_file(new) != want:
                raise RuntimeError("Checksum mismatch, the download was discarded.")
            q.put(("log", "Checksum verified.", "dim"))
            q.put(("log", "Installing and restarting...", "ok"))
            apply_exe_update(new)
            q.put(("quit",))
        except Exception as e:
            q.put(("log", f"Update failed: {clean(e)}", "err"))
            try:
                new.unlink(missing_ok=True)
            except OSError:
                pass
            q.put(("status", "Ready"))
            q.put(("progress", 0))
            q.put(("update_done",))
            if self.pending_restart:
                q.put(("ask_restart", "yt-dlp was updated."))

    def update_source_worker(self, finish=True):
        q = self.q
        repo_dir = Path(__file__).resolve().parent
        try:
            if (repo_dir / ".git").exists() and shutil.which("git"):
                q.put(("log", "Running: git pull --ff-only", "info"))
                code = run_stream(["git", "pull", "--ff-only"], str(repo_dir),
                                  lambda line: q.put(("log", line, "dim")))
                if code == 0:
                    q.put(("log", "Done. Restart the app to use the new code.", "ok"))
                else:
                    q.put(("log", f"git exited with code {code}", "err"))
            elif REPO:
                webbrowser.open(f"https://github.com/{REPO}/releases/latest")
                q.put(("log", "Opened the releases page in your browser.", "info"))
            else:
                q.put(("log", "No GitHub repository is configured (set REPO in app_version.py).", "warn"))
        except Exception as e:
            q.put(("log", f"Update failed: {clean(e)}", "err"))
        if finish:
            q.put(("update_done",))

    def update_packages_worker(self, proxy, finish=True):
        q = self.q
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade"]
        if proxy.startswith("http"):
            cmd += ["--proxy", proxy]
        cmd += PIP_PACKAGES
        lines = []

        def emit(line):
            lines.append(line)
            q.put(("log", line, "dim"))

        try:
            q.put(("log", "Running: pip install --upgrade " + " ".join(PIP_PACKAGES), "info"))
            code = run_stream(cmd, None, emit)
            if code == 0:
                q.put(("log", "Packages updated. Restart the app to use them.", "ok"))
            else:
                q.put(("log", f"pip exited with code {code}", "err"))
                if any("externally-managed" in ln for ln in lines):
                    q.put(("log", "Use a virtual environment: python -m venv .venv, then "
                                  "install with .venv/bin/pip (see README).", "warn"))
        except Exception as e:
            q.put(("log", f"Update failed: {clean(e)}", "err"))
        if finish:
            q.put(("update_done",))

    # ---------- download flow ----------
    def start_download(self):
        if self.updating:
            self.var_status.set("Wait for the update task to finish")
            return
        if self.busy:
            return
        raw = self.var_url.get().strip().strip('"').strip("'")
        if not raw:
            messagebox.showwarning(APP_NAME, "Paste a link first.")
            return

        out_dir = self.out_path()
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            if not os.access(out_dir, os.W_OK):
                raise PermissionError("the folder is not writable")
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Bad save folder: {e}")
            return

        use_proxy = self.sw_proxy.get()
        proxy_raw = self.var_proxy.get().strip()
        if use_proxy and not proxy_raw:
            messagebox.showerror(APP_NAME, "The proxy switch is on but the address is empty.")
            return

        use_cookies = self.sw_cookies.get()
        audio_only = self.sw_audio.get()
        all_browsers = self.sw_all_browsers.get()
        browser = self.var_browser.get().strip().lower()
        profile = self.var_profile.get().strip()
        if profile:
            profile = os.path.expandvars(os.path.expanduser(profile))
        cookies = None
        if use_cookies and browser:
            cookies = (browser, profile) if profile else (browser,)
        # Detected profiles are the automatic fallback when YouTube asks to sign in. Other browsers'
        # cookies are only included if the user switched that option on.
        fallback_cookies = (detect_browser_cookie_sources(browser, profile, all_browsers)
                            if use_cookies else [])

        self.cfg = {
            "out_dir": str(out_dir), "proxy": proxy_raw, "use_proxy": use_proxy,
            "use_cookies": use_cookies, "cookie_all_browsers": all_browsers,
            "browser": browser, "profile": profile, "audio_only": audio_only,
        }
        save_settings(self.cfg)

        proxy = normalize_proxy(proxy_raw) if use_proxy else ""
        self.cancel.clear()
        self.set_busy(True)
        threading.Thread(
            target=self.worker,
            args=(raw, out_dir, proxy, cookies, fallback_cookies, audio_only),
            daemon=True,
        ).start()

    def worker(self, user_input, out_dir, proxy, cookies, fallback_cookies=None, audio_only=False):
        q = self.q

        def L(msg, tag="text"):
            q.put(("log", msg, tag))

        def S(msg):
            q.put(("status", msg))

        def P(pct):
            q.put(("progress", pct))

        temp_list = None
        urls = None
        ok = failed = 0
        try:
            is_file = False
            try:
                is_file = Path(user_input).expanduser().is_file()
            except (OSError, ValueError):
                pass

            if not is_file and is_youtube_playlist(user_input):
                L("Playlist detected. Fetching links...", "info")
                S("Reading playlist...")
                title, links, fetch_err = "playlist", [], ""
                try:
                    title, links = fetch_playlist_links(user_input, proxy, cookies, fallback_cookies)
                except Exception as e:
                    fetch_err = clean(e)
                if not links:
                    # Do not fall back to downloading the playlist URL as one video.
                    reason = fetch_err or "the playlist is empty, private or unavailable"
                    L(f"Could not read the playlist: {reason}", "err")
                    hint = error_hint(reason)
                    if hint:
                        L(hint, "warn")
                    if "v=" in (urlparse(user_input).query or ""):
                        L("To download only this one video, remove the list=... part from the link.", "info")
                    L("Nothing was downloaded. Fix the problem and press DOWNLOAD again.", "warn")
                    return
                temp_list = out_dir / f"playlist_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                try:
                    with temp_list.open("w", encoding="utf-8") as f:
                        f.write(f"# {title}\n" + "\n".join(links) + "\n")
                    L(f'{len(links)} links from "{title}" written to {temp_list.name}', "ok")
                    user_input, is_file = str(temp_list), True
                except Exception as e:
                    temp_list = None
                    L(f"Could not write the link file ({e}); downloading from memory.", "warn")
                    urls = links

            if is_file:
                urls = read_url_file(Path(user_input).expanduser())
                L(f"Loaded {len(urls)} links from {Path(user_input).name}", "info")
            elif urls is None:
                urls = [user_input]

            L(f"Output: {out_dir}", "dim")
            L(f"Proxy: {proxy or 'off'}", "dim")
            if cookies:
                L(f"Cookies: enabled ({cookies})", "dim")
                if fallback_cookies:
                    L(f"Detected {len(fallback_cookies)} browser cookie profile(s) for fallback.", "dim")
                else:
                    L("No additional browser cookie profiles detected.", "dim")
            else:
                L("Cookies: off", "dim")
            S("Downloading...")
            P(0)

            total = len(urls)
            for i, url in enumerate(urls, 1):
                if self.cancel.is_set():
                    L("Cancelled", "warn")
                    break
                L(f"\n[{i}/{total}] {url}", "head")
                P(0)
                prefix = f"[{i}/{total}] " if total > 1 else ""

                def cb(downloaded, size, speed, eta, finished=False, prefix=prefix):
                    if finished:
                        P(100)
                        S(prefix + ("Converting to MP3..." if audio_only else "Merging..."))
                        return
                    if size and size > 0:
                        pct = min(int(downloaded / size * 100), 99)
                        P(pct)
                        msg = f"{prefix}{pct}%  {speed}"
                        if eta:
                            msg += f"  ETA {int(eta)}s"
                        S(msg)
                    else:
                        S(prefix + (speed or "..."))

                try:
                    use_ck, use_imp = cookies, True
                    tried_cookie_sources = set()
                    if use_ck:
                        tried_cookie_sources.add(tuple(use_ck))
                    while True:
                        try:
                            download_video(url, out_dir, proxy, use_ck, progress_callback=cb,
                                           cancel_event=self.cancel, impersonate=use_imp,
                                           retries=5 if use_imp else 20, batch=total > 1,
                                           audio_only=audio_only)
                            break
                        except yt_dlp.utils.DownloadCancelled:
                            raise
                        except Exception as first:
                            m = clean(first).lower()
                            # A pure rate limit is not an auth problem: other cookie profiles will not help.
                            auth_challenge = classify_error(m) in ("login", "rate_login")
                            is_youtube = "youtube.com" in url.lower() or "youtu.be/" in url.lower()
                            cookie_problem = bool(use_ck) and any(k in m for k in (
                                "cookie", "database is locked", "could not copy", "dpapi",
                                "failed to decrypt",
                            ))
                            if is_youtube and (auth_challenge or cookie_problem) and fallback_cookies:
                                next_source = next((tuple(c) for c in fallback_cookies
                                                    if tuple(c) not in tried_cookie_sources), None)
                                if next_source:
                                    tried_cookie_sources.add(next_source)
                                    use_ck = next_source
                                    label = next_source[0] + (f" ({next_source[1]})" if len(next_source) > 1 else "")
                                    L(f"Trying YouTube cookies from detected browser profile: {label}", "warn")
                                else:
                                    # Every detected profile was tried; surface the failure.
                                    L("All detected cookie profiles were tried. Sign in to YouTube in that "
                                      "browser, or turn the proxy on (your IP may be flagged).", "warn")
                                    raise
                            elif is_youtube and auth_challenge:
                                L("No browser cookie profile found. Select the browser and profile folder, "
                                  "turn Cookies on, and sign in to YouTube there.", "warn")
                                raise
                            elif use_imp and any(k in m for k in ("curl:", "http/2", "protocol_error", "stream")):
                                L("Transport error. Retrying without browser impersonation.", "warn")
                                use_imp = False
                            else:
                                raise
                    ok += 1
                    L("Done", "ok")
                except yt_dlp.utils.DownloadCancelled:
                    L("Cancelled", "warn")
                    break
                except Exception as e:
                    failed += 1
                    msg = clean(e)
                    low = msg.lower()
                    hint = error_hint(msg)
                    if hint:
                        L(hint, "warn")
                    err_file = out_dir / f"error_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{i}.txt"
                    try:
                        with err_file.open("w", encoding="utf-8") as f:
                            f.write(redact(f"URL: {url}\n\nError: {msg}\n\n{traceback.format_exc()}"))
                        saved = f"Full error saved to: {err_file}"
                    except Exception:
                        saved = "Could not write the error file"
                    L(f"Failed: {msg}", "err")
                    L(saved, "dim")

            L(f"\nBatch complete: {ok} ok, {failed} failed\n", "ok" if not failed else "warn")
        except Exception as e:
            L(f"Unexpected error: {clean(e)}", "err")
            L(traceback.format_exc(), "dim")
        finally:
            if temp_list is not None:
                try:
                    temp_list.unlink()
                    L(f"Removed {temp_list.name}", "dim")
                except Exception as e:
                    debug_log(f"remove temp list: {e}")
            S(f"Finished: {ok} ok, {failed} failed")
            P(0)
            q.put(("busy", False))


def main():
    global SCALE
    cleanup_old_exe()
    if IS_WIN:
        try:
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("retr0.downloader")
        except Exception as e:
            debug_log(f"dpi/appid: {e}")
    root = tk.Tk()
    for cls in ("Entry", "Text", "TEntry", "TCombobox"):
        root.bind_class(cls, "<Control-KeyPress>", ctrl_shortcut, add="+")
    try:
        SCALE = max(1.0, root.winfo_fpixels("1i") / 96.0)
    except Exception:
        SCALE = 1.0
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
