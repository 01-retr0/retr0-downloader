#!/usr/bin/env python3
"""Retr0 Downloader: desktop GUI around yt-dlp. Created By Retr0."""
import hashlib
import json
import os
import platform
import queue
import random
import re
import shutil
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

# In the packaged app, a newer yt-dlp downloaded by "Update yt-dlp" lives in LIB_DIR and
# takes priority over the copy bundled in the exe. LIB_DIR is wiped whenever the app itself
# is updated, so a new release never runs on top of an older downloaded package.
_BASE = (Path(os.environ.get("APPDATA", Path.home())) if platform.system() == "Windows"
         else Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")))
LIB_DIR = _BASE / "pydownload" / "lib"
if getattr(sys, "frozen", False):
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
except Exception:
    # A broken downloaded package must never stop the app: drop it and use the bundled copy.
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


def default_out_dir() -> Path:
    return Path.home() / "Downloads" / "Retr0Downloader"


def default_profile() -> str:
    """Opera GX profile directory for this OS, or '' if not found."""
    candidates = []
    if IS_WIN:
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            candidates.append(Path(appdata) / "Opera Software" / "Opera GX Stable")
    elif IS_MAC:
        candidates.append(Path.home() / "Library/Application Support/com.operasoftware.OperaGX")
    else:
        candidates.append(Path.home() / ".var/app/com.opera.opera-gx/config/opera-gx")
        candidates.append(Path.home() / ".config/opera-gx")
    for c in candidates:
        if c.exists():
            return str(c)
    return ""


BROWSERS = ["opera", "chrome", "edge", "firefox", "brave", "vivaldi", "chromium"]


def load_settings() -> dict:
    cfg = {
        "out_dir": str(default_out_dir()),
        "proxy": "127.0.0.1:10808",
        "use_proxy": False,
        "use_cookies": False,
        "browser": "opera",
        "profile": default_profile(),
    }
    try:
        with (app_dir() / "settings.json").open("r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_settings(cfg: dict) -> None:
    try:
        d = app_dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / "settings.json").open("w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


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
    try:
        import deno
        return str(deno.find_deno_bin())
    except Exception:
        return ""


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
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        return True
    except Exception:
        return False


_ANSI = re.compile(r"\x1b\[[0-9;]*m|\[[0-9;]*m")


def clean(msg: str) -> str:
    return _ANSI.sub("", str(msg))


# ======================================
# Update helpers
# ======================================
def version_tuple(v) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", str(v)))


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
            except OSError:
                pass


def pick_release_asset(assets) -> dict | None:
    """The release file for this OS: the .exe on Windows, the *-linux binary on Linux."""
    for a in assets or []:
        n = str(a.get("name", "")).lower()
        if IS_WIN and n.endswith(".exe"):
            return a
        if not IS_WIN and n.endswith("-linux"):
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
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        if "404" in str(e):
            raise RuntimeError("No release found. The repository must be public and have a release.") from e
        raise


def download_asset(url: str, dest: Path, proxy: str, progress) -> None:
    from yt_dlp.networking import Request
    with _http_ydl(proxy) as ydl:
        resp = ydl.urlopen(Request(url, headers={
            "User-Agent": "Retr0Downloader", "Accept": "application/octet-stream"}))
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with dest.open("wb") as f:
            while True:
                chunk = resp.read(262144)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                progress(done, total)
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


def _install_wheel(entry: dict, proxy: str) -> None:
    """Download a pure-Python wheel from PyPI, verify its sha256 and unpack it into LIB_DIR."""
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LIB_DIR / ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    try:
        whl = tmp / Path(entry["filename"]).name
        download_asset_raw(entry["url"], whl, proxy)
        want = (entry.get("digests") or {}).get("sha256")
        if not want or sha256_file(whl) != want:
            raise RuntimeError("Checksum mismatch, the download was discarded.")
        stage = tmp / "stage"
        stage.mkdir()
        root = stage.resolve()
        with zipfile.ZipFile(whl) as z:
            for name in z.namelist():
                target = (stage / name).resolve()
                if target != root and root not in target.parents:
                    raise RuntimeError("Unsafe path inside the package, aborted.")
            z.extractall(stage)
        for item in list(stage.iterdir()):
            if item.name.endswith(".data"):
                continue
            if item.name.endswith(".dist-info"):
                for stale in LIB_DIR.glob(item.name.split("-")[0] + "-*.dist-info"):
                    shutil.rmtree(stale, ignore_errors=True)
            dest = LIB_DIR / item.name
            if dest.is_dir():
                shutil.rmtree(dest)
            elif dest.exists():
                dest.unlink()
            shutil.move(str(item), str(dest))
        (LIB_DIR / ".for_app").write_text(VERSION)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def download_asset_raw(url: str, dest: Path, proxy: str) -> None:
    from yt_dlp.networking import Request
    with _http_ydl(proxy) as ydl:
        resp = ydl.urlopen(Request(url, headers={"User-Agent": "Retr0Downloader"}))
        try:
            with Path(dest).open("wb") as f:
                while True:
                    chunk = resp.read(1 << 18)
                    if not chunk:
                        break
                    f.write(chunk)
        finally:
            resp.close()


def update_ytdlp_lib(proxy: str, log) -> bool:
    """Packaged app: fetch the latest yt-dlp (and its pinned yt-dlp-ejs) from PyPI into LIB_DIR.
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
    log(f"Updating yt-dlp {current} -> {latest} ...", "info")
    _install_wheel(wheel, proxy)
    for req in meta["info"].get("requires_dist") or []:
        m = re.match(r"yt-dlp-ejs\s*==\s*([\w.]+)", req)
        if m:
            log(f"Updating yt-dlp-ejs {m[1]} ...", "info")
            w = _pick_wheel(_pypi_json(f"https://pypi.org/pypi/yt-dlp-ejs/{m[1]}/json", proxy).get("urls"))
            if w:
                _install_wheel(w, proxy)
            break
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
        r = subprocess.run(cmd, capture_output=True, text=True)
    except Exception:
        return None
    return r.stdout.strip() if r.returncode == 0 else ""


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


def fetch_playlist_links(url: str, proxy: str, cookies) -> tuple:
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

    try:
        info = attempt(cookies)
    except Exception:
        if not cookies:
            raise
        info = attempt(None)

    links = []
    for e in (info or {}).get("entries") or []:
        if not e:
            continue
        link = e.get("url") or e.get("webpage_url")
        if (not link or not link.startswith("http")) and e.get("id"):
            link = f"https://www.youtube.com/watch?v={e['id']}"
        if link:
            links.append(link)
    return (info or {}).get("title") or "playlist", links


def read_url_file(path: Path) -> list:
    with path.open("r", encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]


# ======================================
# Core download function
# ======================================
class _SilentLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def download_video(url, out_dir, proxy, cookies, progress_callback=None,
                   cancel_event=None, impersonate=True, retries=20):
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
        "format_sort": ["res", "vcodec:av01", "vcodec:vp9.2", "vcodec:vp9", "fps", "br"],
        "merge_output_format": "mkv",
        "outtmpl": str(out_dir / f"{timestamp}_{rand}_%(title)s.%(ext)s"),
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
        "sleep_interval": 2,
        "max_sleep_interval": 6,
        "concurrent_fragment_downloads": 3,
        "socket_timeout": 60,
    }

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
    if proxy:
        opts["proxy"] = proxy

    if impersonate:
        try:
            from yt_dlp.networking.impersonate import ImpersonateTarget
            opts["impersonate"] = ImpersonateTarget.from_str("chrome")
        except Exception:
            pass

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
            progress_callback(None, None, None, None, finished=True)

    opts["progress_hooks"] = [hook]
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


# ======================================
# Widgets
# ======================================
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
        self.lift()
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
        self.create_text(w // 2, ty, text="// ANONYMOUS DOWNLOAD TUNNEL //", fill=PINK,
                         font=self.app.f_bold)

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

        root.title(APP_NAME)
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
            self.write("Deno not found. YouTube downloads may fail without it.", "warn")

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
        tk.Label(top, text="RETR0 // HIGHEST QUALITY DOWNLOADER", fg=GREEN, bg=PANEL,
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
            row=0, column=2, columnspan=2, sticky="ew", padx=(px(10), 0), pady=rp)
        tk.Label(body, text="A video link, a YouTube playlist, or a .txt file with one link per line",
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
        self.btn_upd_pkg = Btn(right, "Update yt-dlp" if FROZEN else "Update packages",
                               self.update_packages, CYAN, font=self.f_bold)
        self.btn_upd_pkg.pack(side="left", padx=(0, px(10)))
        self.btn_upd_app = Btn(right, "Update app", self.update_app, CYAN, font=self.f_bold)
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
            (True, "proxy route ....... " + (self.cfg["proxy"] if self.cfg["use_proxy"] else "direct")),
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
                f.write(f"{datetime.now().isoformat()}\n{text}\n")
        except Exception:
            pass
        self.write(f"Internal error: {val}", "err")

    # ---------- log and queue ----------
    def write(self, text, tag="text"):
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
                                         args=(url, tag, self.current_proxy(), aname, sums_url),
                                         daemon=True).start()
                    else:
                        self.updating = False
                        self.set_update_buttons(True)
                elif kind == "ask_restart":
                    if messagebox.askyesno(APP_NAME, item[1] + "\n\nRestart the app now to use it?"):
                        try:
                            restart_app()
                            self.root.destroy()
                            return
                        except Exception:
                            messagebox.showinfo(APP_NAME, "Please close and reopen the app.")
                elif kind == "quit":
                    self.root.destroy()
                    return
        except queue.Empty:
            pass
        self.root.after(40, self.poll)

    # ---------- download flow ----------
    # ---------- updates ----------
    def current_proxy(self) -> str:
        if self.sw_proxy.get() and self.var_proxy.get().strip():
            return normalize_proxy(self.var_proxy.get())
        return ""

    def set_update_buttons(self, on):
        self.btn_upd_app.set_enabled(on)
        if self.btn_upd_pkg is not None:
            self.btn_upd_pkg.set_enabled(on)

    def _begin_update(self, target, *args) -> bool:
        if self.busy or self.updating:
            self.var_status.set("Wait for the current task to finish")
            return False
        self.updating = True
        self.set_update_buttons(False)
        threading.Thread(target=target, args=args, daemon=True).start()
        return True

    def update_app(self):
        if FROZEN:
            self._begin_update(self.update_check_worker, self.current_proxy(), False)
        else:
            self._begin_update(self.update_source_worker)

    def update_packages(self):
        if FROZEN:
            self._begin_update(self.update_ytdlp_worker, self.current_proxy())
        else:
            self._begin_update(self.update_packages_worker, self.current_proxy())

    def update_ytdlp_worker(self, proxy):
        q = self.q
        try:
            q.put(("status", "Checking yt-dlp..."))
            if update_ytdlp_lib(proxy, lambda m, t="text": q.put(("log", m, t))):
                q.put(("ask_restart", "yt-dlp was updated."))
        except Exception as e:
            q.put(("log", f"Update failed: {clean(e)}", "err"))
        q.put(("status", "Ready"))
        q.put(("update_done",))

    def auto_check(self):
        if FROZEN and REPO and not self.busy and not self.updating:
            self._begin_update(self.update_check_worker, self.current_proxy(), True)

    def update_check_worker(self, proxy, silent):
        q = self.q
        try:
            rel = fetch_latest_release(proxy)
            tag = str(rel.get("tag_name", ""))
            if version_tuple(tag) <= version_tuple(VERSION):
                if not silent:
                    q.put(("log", f"You are up to date (version {VERSION}).", "ok"))
                q.put(("update_done",))
                return
            asset = pick_release_asset(rel.get("assets", []))
            if not asset:
                q.put(("log", f"Version {tag} exists but has no build for your system.", "warn"))
                q.put(("update_done",))
                return
            sums = next((a for a in rel.get("assets", []) if a.get("name") == "SHA256SUMS.txt"), None)
            q.put(("ask_update", tag, asset["browser_download_url"], int(asset.get("size") or 0),
                   asset["name"], sums["browser_download_url"] if sums else ""))
        except Exception as e:
            if not silent:
                q.put(("log", f"Update check failed: {clean(e)}", "err"))
            q.put(("update_done",))

    def download_update_worker(self, url, tag, proxy, aname="", sums_url=""):
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
            download_asset(url, new, proxy, prog)
            if sums_url:
                sums_file = new.with_name(new.name + ".sums")
                try:
                    download_asset_raw(sums_url, sums_file, proxy)
                    want = parse_sums(sums_file.read_text(encoding="utf-8", errors="replace")).get(aname)
                finally:
                    sums_file.unlink(missing_ok=True)
                if want and sha256_file(new) != want:
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

    def update_source_worker(self):
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
        q.put(("update_done",))

    def update_packages_worker(self, proxy):
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
        q.put(("update_done",))

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
        browser = self.var_browser.get().strip().lower()
        profile = self.var_profile.get().strip()
        cookies = None
        if use_cookies and browser:
            cookies = (browser, profile) if profile else (browser,)

        self.cfg = {
            "out_dir": str(out_dir), "proxy": proxy_raw, "use_proxy": use_proxy,
            "use_cookies": use_cookies, "browser": browser, "profile": profile,
        }
        save_settings(self.cfg)

        proxy = normalize_proxy(proxy_raw) if use_proxy else ""
        self.cancel.clear()
        self.set_busy(True)
        threading.Thread(target=self.worker, args=(raw, out_dir, proxy, cookies), daemon=True).start()

    def worker(self, user_input, out_dir, proxy, cookies):
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
                links, title = [], "playlist"
                try:
                    title, links = fetch_playlist_links(user_input, proxy, cookies)
                except Exception as e:
                    L(f"Playlist fetch failed: {clean(e)}", "warn")
                if links:
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
                else:
                    L("No videos found. Trying the link as a single video.", "warn")

            if is_file:
                urls = read_url_file(Path(user_input).expanduser())
                L(f"Loaded {len(urls)} links from {Path(user_input).name}", "info")
            elif urls is None:
                urls = [user_input]

            L(f"Output: {out_dir}", "dim")
            L(f"Proxy: {proxy or 'off'}", "dim")
            L(f"Cookies: {cookies if cookies else 'off'}", "dim")
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
                        S(prefix + "Merging...")
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
                    while True:
                        try:
                            download_video(url, out_dir, proxy, use_ck, progress_callback=cb,
                                           cancel_event=self.cancel, impersonate=use_imp,
                                           retries=5 if use_imp else 20)
                            break
                        except yt_dlp.utils.DownloadCancelled:
                            raise
                        except Exception as first:
                            m = clean(first).lower()
                            if use_ck and "cookie" in m:
                                L("Cookie load failed. Retrying without cookies.", "warn")
                                use_ck = None
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
                    if "timed out" in low or "timeout" in low or "curl: (28)" in low:
                        L("Timeout. Turn the proxy on or check its address and port.", "warn")
                    elif "curl: (6)" in low or "could not resolve" in low:
                        L("Cannot reach the site. Check your internet connection or turn the proxy on.", "warn")
                    elif "login" in low or "rate-limit" in low or "sign in" in low:
                        L("Login required. Turn Cookies on and be signed in within that browser.", "warn")
                    elif "ffmpeg" in low:
                        L("FFmpeg is missing or not working.", "warn")
                    err_file = out_dir / f"error_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                    try:
                        with err_file.open("w", encoding="utf-8") as f:
                            f.write(f"URL: {url}\n\nError: {msg}\n\n{traceback.format_exc()}")
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
                except Exception:
                    pass
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
        except Exception:
            pass
    root = tk.Tk()
    try:
        SCALE = max(1.0, root.winfo_fpixels("1i") / 96.0)
    except Exception:
        SCALE = 1.0
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
