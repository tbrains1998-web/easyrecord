"""EasyRecord - a simple, free screen recorder.

Pick what to record, set a quality %, press REC (or F12). F11 = screenshot.
Videos are saved as MP4 in Videos/EasyRecord inside your user folder.
Run with:  python easyrecord.py   (needs ffmpeg.exe next to it or on PATH)
"""

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

IS_WINDOWS = sys.platform.startswith("win")

try:
    import loopback          # records computer sound (Windows only)
except Exception:
    loopback = None

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    # Make screen coordinates match real pixels on high-DPI (scaled) displays.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# What to record
# ---------------------------------------------------------------------------

@dataclass
class Target:
    """Describes what part of the screen to record."""
    kind: str                  # "screen", "window" or "region"
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    window_title: str = ""

    def describe(self) -> str:
        if self.kind == "screen":
            return f"Full screen ({self.width} x {self.height})"
        if self.kind == "window":
            return f"Window: {self.window_title}"
        return f"Box at ({self.x}, {self.y}), {self.width} x {self.height}"

    def ffmpeg_input_args(self, fps: int = 30) -> list:
        """FFmpeg arguments that capture this target."""
        rate = ["-framerate", str(fps)]
        if self.kind == "window" and not self.width:
            return ["-f", "gdigrab", *rate, "-i", f"title={self.window_title}"]
        # Windows are recorded by their spot on screen: grabbing the window
        # itself gives a black video for browsers, games and other GPU apps.
        # Video encoders need even sizes.
        w, h = self.width - self.width % 2, self.height - self.height % 2
        if not IS_WINDOWS:  # Linux, used for testing
            display = os.environ.get("DISPLAY", ":0")
            return ["-f", "x11grab", *rate, "-video_size", f"{w}x{h}",
                    "-i", f"{display}+{self.x},{self.y}"]
        return ["-f", "gdigrab", *rate,
                "-offset_x", str(self.x), "-offset_y", str(self.y),
                "-video_size", f"{w}x{h}", "-i", "desktop"]


# ---------------------------------------------------------------------------
# Quality: turn a simple 0-100% into video settings
# ---------------------------------------------------------------------------

def quality_to_crf(percent: int) -> int:
    """100% = near-perfect (big file), 0% = small blurry file.

    CRF is x264's "how much detail to throw away" number: lower = better.
    """
    percent = max(0, min(100, int(percent)))
    return round(40 - percent * 0.24)   # 0% -> 40, 50% -> 28, 100% -> 16


# ---------------------------------------------------------------------------
# Settings (remembered between runs)
# ---------------------------------------------------------------------------

SETTINGS_FILE = Path.home() / ".easyrecord.json"
DEFAULTS = {"quality": 80, "fps": 30, "output_folder": "",
            "image_format": "png", "always_on_top": False,
            "show_settings": False, "mic_on": False, "mic": "",
            "sound_on": True, "sound": "", "sound_name": ""}


def load_settings() -> dict:
    try:
        return {**DEFAULTS, **json.loads(SETTINGS_FILE.read_text())}
    except Exception:
        return dict(DEFAULTS)


def save_settings(settings: dict):
    try:
        SETTINGS_FILE.write_text(json.dumps(settings))
    except Exception:
        pass


def default_folder() -> Path:
    return Path.home() / "Videos" / "EasyRecord"


def videos_folder(chosen: str = "") -> Path:
    folder = Path(chosen) if chosen else default_folder()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def find_ffmpeg():
    """Look next to the app first (for the packaged version), then on PATH."""
    here = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    exe_dir = Path(sys.executable).resolve().parent
    for folder in (here, exe_dir):
        for name in ("ffmpeg.exe", "ffmpeg"):
            if (folder / name).is_file():
                return str(folder / name)
    return shutil.which("ffmpeg")


# ---------------------------------------------------------------------------
# Recorder: runs FFmpeg in the background
# ---------------------------------------------------------------------------

# Roughly how long FFmpeg takes from launch to grabbing its first picture.
VIDEO_START_DELAY = 0.0


class Recorder:
    def __init__(self, ffmpeg_path: str):
        self.ffmpeg = ffmpeg_path
        self.proc = None
        self.output = None
        self.log_file = None
        self.sound = None

    @property
    def is_recording(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, target: Target, quality: int, fps: int, folder: Path,
              mic: str = "", sound=None, output: Path = None) -> Path:
        """mic = microphone name or "". sound = output device id, "" for the
        Windows default, or None for no computer sound."""
        stamp = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
        self.output = output or folder / f"EasyRecord {stamp}.mp4"
        audio_in, port = [], None
        if sound is not None and loopback:
            try:
                self.sound = loopback.LoopbackCapture(sound)
                port = loopback.free_port()
                audio_in += self.sound.ffmpeg_args(port)
            except Exception:
                self.sound = None
        if mic:
            audio_in += ["-thread_queue_size", "1024", "-f", "dshow",
                         "-audio_buffer_size", "50", "-i", f"audio={mic}"]
        tracks = audio_in.count("-i")
        if tracks == 2:   # mix computer sound and mic into one track
            audio_out = ["-filter_complex",
                         "[1:a][2:a]amix=inputs=2:duration=first:normalize=0[a]",
                         "-map", "0:v", "-map", "[a]"]
        elif tracks == 1:
            audio_out = ["-map", "0:v", "-map", "1:a"]
        else:
            audio_out = []
        if tracks:
            audio_out += ["-c:a", "aac", "-b:a", "192k"]
        cmd = [self.ffmpeg, "-hide_banner", "-y",
               "-thread_queue_size", "512", *target.ffmpeg_input_args(fps),
               *audio_in,
               *audio_out,
               "-c:v", "libx264", "-preset", "veryfast",
               "-crf", str(quality_to_crf(quality)),
               "-pix_fmt", "yuv420p",
               # Keep sizes even even when recording a window.
               "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
               "-movflags", "+faststart",
               str(self.output)]
        self.log_file = open(self.output.with_suffix(".log"), "w")
        started = time.perf_counter()
        flags = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                     stdout=self.log_file,
                                     stderr=subprocess.STDOUT,
                                     creationflags=flags)
        if self.sound:
            self.sound.start(port, ready=lambda: self._log_has("Press [q]"),
                             video_start=started + VIDEO_START_DELAY)
        return self.output

    def _log_has(self, text: str) -> bool:
        """Has FFmpeg written this to its log yet?"""
        try:
            return text in self.output.with_suffix(".log").read_text(errors="ignore")
        except OSError:
            return False

    def stop(self) -> bool:
        """Ask FFmpeg to finish the file nicely. Returns True if it worked."""
        if not self.proc:
            return False
        if self.sound:
            self.sound.stop()
            self.sound = None
        try:
            self.proc.stdin.write(b"q")
            self.proc.stdin.flush()
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        ok = self.output.exists() and self.output.stat().st_size > 0
        self.proc = None
        self.log_file.close()
        if ok:  # keep the log only when something went wrong
            try:
                self.output.with_suffix(".log").unlink()
            except Exception:
                pass
        return ok

    def error_text(self) -> str:
        try:
            lines = self.output.with_suffix(".log").read_text().strip().splitlines()
            return "\n".join(lines[-5:])
        except Exception:
            return ""


def list_microphones(ffmpeg: str) -> list:
    """Names of the microphones Windows knows about (asks FFmpeg)."""
    if not (IS_WINDOWS and ffmpeg):
        return []
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-list_devices", "true",
                              "-f", "dshow", "-i", "dummy"],
                             capture_output=True, text=True, errors="replace",
                             timeout=10, stdin=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW).stderr
    except Exception:
        return []
    mics = []
    for line in out.splitlines():
        if line.rstrip().endswith("(audio)") and '"' in line:
            mics.append(line.split('"')[1])
    return mics


def join_parts(ffmpeg: str, parts: list, output: Path) -> bool:
    """Glue the pieces of a paused recording into one video (no re-encoding)."""
    if len(parts) == 1:
        parts[0].replace(output)
        return True
    listing = output.with_suffix(".txt")
    listing.write_text("".join(
        "file '" + str(p).replace("'", "'\\''") + "'\n" for p in parts), encoding="utf-8")
    flags = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
    try:
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "concat", "-safe", "0", "-i", str(listing),
                        "-c", "copy", "-movflags", "+faststart", str(output)],
                       timeout=600, creationflags=flags, stdin=subprocess.DEVNULL,
                       capture_output=True)
    except Exception:
        return False
    finally:
        listing.unlink(missing_ok=True)
    ok = output.exists() and output.stat().st_size > 0
    if ok:
        for part in parts:
            part.unlink(missing_ok=True)
    return ok


def take_screenshot(ffmpeg: str, target: Target, path: Path) -> bool:
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
           *target.ffmpeg_input_args(), "-frames:v", "1"]
    if path.suffix == ".jpg":
        cmd += ["-q:v", "2"]
    flags = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
    try:
        subprocess.run(cmd + [str(path)], timeout=15, creationflags=flags,
                       stdin=subprocess.DEVNULL, capture_output=True)
    except Exception:
        return False
    return path.exists() and path.stat().st_size > 0


# ---------------------------------------------------------------------------
# Hotkeys that works even when the app is in the background (Windows)
# ---------------------------------------------------------------------------

VK_F9, VK_F11, VK_F12 = 0x78, 0x7A, 0x7B


def start_global_hotkeys(events: "queue.Queue", keys: dict):
    """keys maps a key code to an event name, e.g. {VK_F12: "toggle"}.
    The event name goes into the queue each time that key is pressed.
    Keys Windows won't give us as a hotkey (F12 is kept for debuggers, and
    other apps may own some) are watched directly instead, so they still
    work from anywhere, games included."""
    if not IS_WINDOWS:
        return

    def listen():
        user32 = ctypes.windll.user32
        names, watched = {}, {}
        for hotkey_id, (vk, name) in enumerate(keys.items(), start=1):
            if user32.RegisterHotKey(None, hotkey_id, 0x4000, vk):  # 0x4000 = no repeat
                names[hotkey_id] = name
            else:
                watched[vk] = name
        if watched:
            threading.Thread(target=watch, args=(watched,), daemon=True).start()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == 0x0312 and msg.wParam in names:  # WM_HOTKEY
                events.put(names[msg.wParam])

    def watch(watched):
        user32 = ctypes.windll.user32
        down = {vk: False for vk in watched}
        while True:
            for vk, name in watched.items():
                is_down = bool(user32.GetAsyncKeyState(vk) & 0x8000)
                if is_down and not down[vk]:
                    events.put(name)
                down[vk] = is_down
            time.sleep(0.02)

    threading.Thread(target=listen, daemon=True).start()


# ---------------------------------------------------------------------------
# Listing open windows (Windows only)
# ---------------------------------------------------------------------------

def list_displays() -> list:
    """[(name, (x, y, width, height), is_main)] for each monitor, left to right."""
    if not IS_WINDOWS:
        return []
    user32 = ctypes.windll.user32
    found = []

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                        ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    def on_monitor(hmon, _hdc, _rect, _):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        user32.GetMonitorInfoW(hmon, ctypes.byref(info))
        r = info.rcMonitor
        found.append(((r.left, r.top, r.right - r.left, r.bottom - r.top),
                      bool(info.dwFlags & 1)))
        return True

    user32.EnumDisplayMonitors(None, None, on_monitor, 0)
    found.sort(key=lambda d: (d[0][0], d[0][1]))
    return [(f"Display {i}", rect, main) for i, (rect, main) in enumerate(found, 1)]


def windows_under_overlay() -> list:
    """[(title, (left, top, right, bottom))] of visible app windows, top-most first."""
    user32 = ctypes.windll.user32
    dwm = ctypes.windll.dwmapi
    my_pid = os.getpid()
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def on_window(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == my_pid:
            return True
        if user32.GetWindowLongW(hwnd, -20) & 0x80:   # tool windows (Start button etc.)
            return True
        cloaked = wintypes.DWORD()   # hidden UWP / other-desktop windows
        dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4)
        if cloaked.value:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if not title.strip() or title == "Program Manager":
            return True
        rect = wintypes.RECT()   # the visible frame, without the invisible border
        if dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)):
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if rect.right - rect.left > 20 and rect.bottom - rect.top > 20:
            found.append((title, (rect.left, rect.top, rect.right, rect.bottom)))
        return True

    user32.EnumWindows(on_window, 0)
    return found


# ---------------------------------------------------------------------------
# Look and feel (dark theme)
# ---------------------------------------------------------------------------

FONT = "Segoe UI"
C = {
    "top": "#262b34",      # header and toolbar
    "status": "#3a4252",   # bar under the toolbar
    "side": "#2b313c",     # left menu
    "bg": "#313846",       # page background
    "field": "#3f4757",    # boxes and plain buttons
    "field_hi": "#4b5466",
    "line": "#4a5263",
    "text": "#e3e7ee",
    "dim": "#9aa3b2",
    "blue": "#4a74c4",
    "blue_hi": "#5a86d8",
    "red": "#e53935",
    "red_hi": "#f0504c",
}


# Screen scale: 1.0 at 100% Windows scaling, 2.0 at 200% (e.g. 4K TVs).
# Fonts scale on their own; every pixel size goes through px() so it keeps up.
S = 1.0


def px(n):
    return int(round(n * S))


def init_scale(root):
    global S
    S = max(1.0, root.winfo_fpixels("1i") / 96.0)


APP_ID = "EasyRecord.ScreenRecorder"

if IS_WINDOWS:
    # Own taskbar group, so Windows shows our icon and not Python's.
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def resource(name: str) -> Path:
    """A file shipped with the app (inside the .exe when packaged)."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / name


def set_app_icon(root):
    """Our icon on the title bar, the taskbar and every pop-up window."""
    try:
        if IS_WINDOWS:
            root.iconbitmap(default=str(resource("easyrecord.ico")))
        else:
            root.iconphoto(True, tk.PhotoImage(file=str(resource("icon.png"))))
    except Exception:
        pass


def flat_button(parent, text, command, bg=None, width=None, **kw):
    bg = bg or C["field"]
    hi = C["blue_hi"] if bg == C["blue"] else C["field_hi"]
    b = tk.Button(parent, text=text, command=command, bg=bg, fg=C["text"],
                  activebackground=hi, activeforeground=C["text"], relief="flat",
                  highlightthickness=0, bd=0, font=(FONT, 9), padx=px(8), pady=px(3),
                  cursor="hand2", **kw)
    if width:
        b.config(width=width)
    b.bind("<Enter>", lambda e: b.config(bg=hi) if b["state"] == "normal" else None)
    b.bind("<Leave>", lambda e: b.config(bg=bg))
    return b


# ---------------------------------------------------------------------------
# Drag-a-box overlay
# ---------------------------------------------------------------------------

def virtual_desktop(root):
    """(x, y, width, height) of all monitors together."""
    if IS_WINDOWS:
        u = ctypes.windll.user32
        return (u.GetSystemMetrics(76), u.GetSystemMetrics(77),
                u.GetSystemMetrics(78), u.GetSystemMetrics(79))
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def window_target(title, rect):
    """A window Target, trimmed to the part that is actually on screen."""
    l, t, r, b = rect
    if IS_WINDOWS:
        u = ctypes.windll.user32
        vx, vy = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
        vr, vb = vx + u.GetSystemMetrics(78), vy + u.GetSystemMetrics(79)
        l, t, r, b = max(l, vx), max(t, vy), min(r, vr), min(b, vb)
    return Target("window", l, t, max(r - l, 2), max(b - t, 2), window_title=title)


class BoxSelector:
    """Dims the screen and lets the user drag a box. Calls on_done(Target or None)."""

    MIN_SIZE = 20

    def __init__(self, root, on_done):
        self.on_done = on_done
        self.start = None
        self.rect = None

        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.3)
        # Cover every monitor, not just the main one.
        self.ox, self.oy, sw, sh = virtual_desktop(root)
        self.win.geometry(f"{sw}x{sh}+{self.ox}+{self.oy}")

        self.canvas = tk.Canvas(self.win, bg="black", highlightthickness=0,
                                cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        main_x, main_w = -self.ox, root.winfo_screenwidth()   # hint on the main display
        self.canvas.create_text(main_x + main_w // 2, px(40), fill="white",
                                font=("Segoe UI", 18, "bold"),
                                text="Drag a box to record.  Esc to cancel.")

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.win.bind("<Escape>", lambda e: self._finish(None))
        self.win.focus_force()

    def _press(self, e):
        self.start = (e.x_root, e.y_root)
        if self.rect:
            self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(e.x, e.y, e.x, e.y,
                                                 outline="red", width=3)

    def _drag(self, e):
        if self.start:
            sx = self.start[0] - self.win.winfo_rootx()
            sy = self.start[1] - self.win.winfo_rooty()
            self.canvas.coords(self.rect, sx, sy, e.x, e.y)

    def _release(self, e):
        if not self.start:
            return
        x1, y1 = self.start
        x2, y2 = e.x_root, e.y_root
        x, y = min(x1, x2), min(y1, y2)
        w, h = abs(x2 - x1), abs(y2 - y1)
        if w < self.MIN_SIZE or h < self.MIN_SIZE:
            self.start = None  # too small, let them try again
            return
        self._finish(Target("region", x, y, w, h))

    def _finish(self, target):
        self.win.destroy()
        self.on_done(target)


# ---------------------------------------------------------------------------
# Click-a-window overlay
# ---------------------------------------------------------------------------

class WindowPicker:
    """Hover over any window to outline it, click to pick it. Esc cancels.

    Calls on_done(Target or None).
    """

    def __init__(self, root, on_done):
        self.on_done = on_done
        self.windows = windows_under_overlay()
        self.hit = None

        # The whole desktop, across every monitor.
        self.ox, self.oy, w, h = virtual_desktop(root)

        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.3)
        self.win.geometry(f"{w}x{h}+{self.ox}+{self.oy}")

        self.canvas = tk.Canvas(self.win, bg="black", highlightthickness=0,
                                cursor="hand2")
        self.canvas.pack(fill="both", expand=True)
        self.hint = self.canvas.create_text(
            -self.ox + root.winfo_screenwidth() // 2, px(40), fill="white",
            font=(FONT, 18, "bold"),
            text="Click the window you want to record.  Esc to cancel.")
        self.box = self.canvas.create_rectangle(0, 0, 0, 0, outline="red",
                                                width=px(5), state="hidden")
        self.label = self.canvas.create_text(0, 0, fill="white", anchor="sw",
                                             font=(FONT, 14, "bold"), state="hidden")

        self.canvas.bind("<Motion>", self._move)
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Button-3>", lambda e: self._finish(None))
        self.win.bind("<Escape>", lambda e: self._finish(None))
        self.win.focus_force()

    def _window_at(self, x, y):
        for title, (l, t, r, b) in self.windows:   # top-most first
            if l <= x < r and t <= y < b:
                return title, (l, t, r, b)
        return None

    def _move(self, e):
        self.hit = self._window_at(e.x_root, e.y_root)
        if not self.hit:
            self.canvas.itemconfig(self.box, state="hidden")
            self.canvas.itemconfig(self.label, state="hidden")
            return
        title, (l, t, r, b) = self.hit
        l, t, r, b = l - self.ox, t - self.oy, r - self.ox, b - self.oy
        self.canvas.coords(self.box, l, t, r, b)
        self.canvas.coords(self.label, l + px(10), t + px(36))
        self.canvas.itemconfig(self.label, text=title)
        self.canvas.itemconfig(self.box, state="normal")
        self.canvas.itemconfig(self.label, state="normal")

    def _click(self, e):
        self.hit = self._window_at(e.x_root, e.y_root)
        if self.hit:
            title, (l, t, r, b) = self.hit
            self._finish(window_target(title, (l, t, r, b)))

    def _finish(self, target):
        self.win.destroy()
        self.on_done(target)


# ---------------------------------------------------------------------------
# Toolbar pieces
# ---------------------------------------------------------------------------

def draw_icon(cv, kind, cx, cy, color, k=1.0):
    """Small line-drawn icons so we need no image files. k = extra size factor."""
    before = set(cv.find_all())
    _draw_icon(cv, kind, cx, cy, color)
    for item in set(cv.find_all()) - before:
        cv.scale(item, cx, cy, S * k, S * k)
        try:
            w = float(cv.itemcget(item, "width"))
            cv.itemconfig(item, width=max(1, w * S * k))
        except (tk.TclError, ValueError):
            pass


def _draw_icon(cv, kind, cx, cy, color):
    if kind == "screen":
        cv.create_rectangle(cx - 22, cy - 15, cx + 22, cy + 13, outline=color, width=3)
        cv.create_line(cx, cy + 13, cx, cy + 19, fill=color, width=3)
        cv.create_line(cx - 10, cy + 20, cx + 10, cy + 20, fill=color, width=3)
    elif kind == "window":
        cv.create_rectangle(cx - 22, cy - 16, cx + 22, cy + 16, outline=color, width=3)
        cv.create_rectangle(cx - 22, cy - 16, cx + 22, cy - 8, fill=color, outline=color)
    elif kind == "area":
        cv.create_rectangle(cx - 22, cy - 16, cx + 22, cy + 16, outline=color,
                            width=3, dash=(5, 4))
        cv.create_line(cx + 8, cy + 2, cx + 8, cy + 26, fill=color, width=2)
        cv.create_line(cx - 4, cy + 14, cx + 20, cy + 14, fill=color, width=2)
    elif kind == "mic":
        cv.create_oval(cx - 7, cy - 18, cx + 7, cy - 8, fill=color, outline=color)
        cv.create_rectangle(cx - 7, cy - 13, cx + 7, cy + 2, fill=color, outline=color)
        cv.create_oval(cx - 7, cy - 4, cx + 7, cy + 6, fill=color, outline=color)
        cv.create_arc(cx - 13, cy - 12, cx + 13, cy + 12, start=200, extent=140,
                      style="arc", outline=color, width=3)
        cv.create_line(cx, cy + 12, cx, cy + 18, fill=color, width=3)
        cv.create_line(cx - 8, cy + 18, cx + 8, cy + 18, fill=color, width=3)
    elif kind == "speaker":
        cv.create_polygon(cx - 16, cy - 6, cx - 9, cy - 6, cx + 1, cy - 15,
                          cx + 1, cy + 15, cx - 9, cy + 6, cx - 16, cy + 6,
                          fill=color, outline=color)
        cv.create_arc(cx - 4, cy - 9, cx + 12, cy + 9, start=-50, extent=100,
                      style="arc", outline=color, width=3)
        cv.create_arc(cx - 8, cy - 16, cx + 18, cy + 16, start=-50, extent=100,
                      style="arc", outline=color, width=3)
    elif kind == "pause":
        cv.create_rectangle(cx - 10, cy - 13, cx - 3, cy + 13, fill=color, outline=color)
        cv.create_rectangle(cx + 3, cy - 13, cx + 10, cy + 13, fill=color, outline=color)
    elif kind == "resume":
        cv.create_polygon(cx - 9, cy - 14, cx + 13, cy, cx - 9, cy + 14,
                          fill=color, outline=color)
    elif kind == "camera":
        cv.create_rectangle(cx - 16, cy - 9, cx + 16, cy + 12, fill=color, outline=color)
        cv.create_rectangle(cx - 6, cy - 14, cx + 6, cy - 8, fill=color, outline=color)
        cv.create_oval(cx - 7, cy - 5, cx + 7, cy + 9, fill=C["top"], outline=C["top"])
        cv.create_oval(cx - 4, cy - 2, cx + 4, cy + 6, fill=color, outline=color)


class ModeButton(tk.Canvas):
    """A big toolbar button with an icon and a label. Blue when chosen."""

    def __init__(self, parent, kind, label, command):
        self.W, self.H = px(62), px(52)
        super().__init__(parent, width=self.W, height=self.H, bg=C["top"],
                         highlightthickness=0, cursor="hand2")
        self.kind, self.label, self.command = kind, label, command
        self.selected = False
        self.enabled = True
        self.hover = False
        self.bind("<Button-1>", lambda e: self.enabled and self.command())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.draw()

    def _set_hover(self, on):
        self.hover = on
        self.draw()

    def set_selected(self, on):
        self.selected = on
        self.draw()

    def set_enabled(self, on):
        self.enabled = on
        self.draw()

    def draw(self):
        self.delete("all")
        if self.selected:
            bg = C["blue_hi"] if self.hover and self.enabled else C["blue"]
        else:
            bg = C["field"] if self.hover and self.enabled else C["top"]
        self.create_rectangle(0, 0, self.W, self.H, fill=bg, outline=bg)
        color = "white" if self.enabled else C["dim"]
        draw_icon(self, self.kind, self.W // 2, px(20), color, k=0.55)
        self.create_text(self.W // 2, px(42), text=self.label, fill=color,
                         font=(FONT, 7, "bold"))


class Slider(tk.Canvas):
    """A chunky slider that is easy to grab: thick bar, big round handle.

    Click anywhere on the bar to jump there, drag, or use the mouse wheel.
    """

    def __init__(self, parent, variable, low, high, step, command=None, length=240):
        self.W, self.H = px(length), px(30)
        super().__init__(parent, width=self.W, height=self.H, bg=C["bg"],
                         highlightthickness=0, cursor="hand2")
        self.var, self.low, self.high, self.step = variable, low, high, step
        self.command = command
        self.enabled = True
        self.hover = False
        self.r = px(11)              # handle radius
        self.bind("<Button-1>", self._mouse)
        self.bind("<B1-Motion>", self._mouse)
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.draw()

    def config(self, **kw):
        if "state" in kw:
            self.enabled = kw.pop("state") == "normal"
            self.configure(cursor="hand2" if self.enabled else "arrow")
            self.draw()
        if kw:
            super().config(**kw)

    def _x_for(self, value):
        span = self.W - 2 * self.r
        return self.r + span * (value - self.low) / (self.high - self.low)

    def _set(self, value):
        value = round(value / self.step) * self.step
        value = max(self.low, min(self.high, value))
        if value != self.var.get():
            self.var.set(value)
            if self.command:
                self.command()
        self.draw()

    def _mouse(self, e):
        if self.enabled:
            span = self.W - 2 * self.r
            self._set(self.low + (e.x - self.r) / span * (self.high - self.low))

    def _wheel(self, e):
        if self.enabled:
            self._set(self.var.get() + (self.step if e.delta > 0 else -self.step))

    def _set_hover(self, on):
        self.hover = on
        self.draw()

    def draw(self):
        self.delete("all")
        cy, t = self.H / 2, px(5)    # bar half-thickness
        x = self._x_for(self.var.get())
        fill = (C["blue_hi"] if self.hover else C["blue"]) if self.enabled else C["line"]
        self.create_line(self.r, cy, self.W - self.r, cy, fill=C["field"],
                         width=2 * t, capstyle="round")
        self.create_line(self.r, cy, x, cy, fill=fill, width=2 * t, capstyle="round")
        r = self.r + (px(1) if self.hover and self.enabled else 0)
        self.create_oval(x - r, cy - r, x + r, cy + r, fill="white" if self.enabled
                         else C["dim"], outline=fill, width=px(3))


class RecButton(tk.Canvas):
    """The big round red button."""

    def __init__(self, parent, command):
        self.SIZE = px(46)
        super().__init__(parent, width=self.SIZE, height=self.SIZE, bg=C["top"],
                         highlightthickness=0, cursor="hand2")
        self.command = command
        self.recording = False
        self.hover = False
        self.bind("<Button-1>", lambda e: self.command())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.draw()

    def _set_hover(self, on):
        self.hover = on
        self.draw()

    def set_recording(self, on):
        self.recording = on
        self.draw()

    def draw(self):
        self.delete("all")
        s = self.SIZE
        fill = C["red_hi"] if self.hover else C["red"]
        self.create_oval(px(2), px(2), s - px(2), s - px(2), fill=fill, outline="#1c2027",
                         width=px(2))
        if self.recording:
            self.create_rectangle(s / 2 - px(7), s / 2 - px(7), s / 2 + px(7), s / 2 + px(7),
                                  fill="white", outline="white")
        else:
            self.create_text(s / 2, s / 2, text="REC", fill="white",
                             font=(FONT, 9, "bold"))


class IconButton(tk.Canvas):
    """A small toolbar button: a drawn icon, or a text symbol like a gear."""

    def __init__(self, parent, kind, command, size=32, glyph=None):
        size = px(size)
        super().__init__(parent, width=size, height=size, bg=C["top"],
                         highlightthickness=0, cursor="hand2")
        self.kind, self.size, self.glyph = kind, size, glyph
        self.enabled, self.hover, self.color = True, False, None
        self.bind("<Button-1>", lambda e: self.enabled and command())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.draw()

    def _set_hover(self, on):
        self.hover = on
        self.draw()

    def set_kind(self, kind, color=None):
        self.kind, self.color = kind, color
        self.draw()

    def set_enabled(self, on):
        self.enabled = on
        self.configure(cursor="hand2" if on else "arrow")
        self.draw()

    def draw(self, color=None):
        self.delete("all")
        if not self.enabled:
            color = "#4b5262"
        elif self.color:
            color = self.color
        else:
            color = "white" if self.hover else C["dim"]
        c = self.size // 2
        if self.glyph:
            self.create_text(c, c, text=self.glyph, fill=color, font=(FONT, 12))
        else:
            draw_icon(self, self.kind, c, c, color, k=0.6)


class SoundButton(tk.Canvas):
    """Mic or speaker icon: click to turn on/off. The small arrow picks the device."""

    def __init__(self, parent, kind, on_toggle, on_menu):
        self.kind = kind
        self.W, self.H = px(44), px(32)
        super().__init__(parent, width=self.W, height=self.H, bg=C["top"],
                         highlightthickness=0, cursor="hand2")
        self.on = False
        self.hover = False
        self.on_toggle, self.on_menu = on_toggle, on_menu
        self.bind("<Button-1>", self._click)
        self.bind("<Button-3>", lambda e: self.on_menu())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.draw()

    def _click(self, e):
        if e.x > px(30):     # the arrow part
            self.on_menu()
        else:
            self.on_toggle()

    def _set_hover(self, on):
        self.hover = on
        self.draw()

    def set_on(self, on):
        self.on = on
        self.draw()

    def draw(self):
        self.delete("all")
        if self.on:
            color = "#81c784"
        else:
            color = "white" if self.hover else C["dim"]
        cx, cy = px(16), self.H // 2
        draw_icon(self, self.kind, cx, cy, color, k=0.6)
        if not self.on:   # red slash = muted
            d = px(10)
            self.create_line(cx - d, cy - d, cx + d, cy + d, fill=C["red"], width=px(2))
        a = px(37)
        self.create_polygon(a - px(4), cy - px(2), a + px(4), cy - px(2), a, cy + px(3),
                            fill="white" if self.hover else C["dim"], outline="")


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

PAGES = [("home", "\u2302", "Home"), ("general", "\u2699", "General"),
         ("video", "\u25b6", "Video"), ("image", "\u25a3", "Image"),
         ("about", "\u24d8", "About")]


class App:
    def __init__(self):
        self.root = tk.Tk()
        init_scale(self.root)
        self.root.title("EasyRecord")
        self.root.configure(bg=C["bg"])
        self.root.resizable(False, False)
        set_app_icon(self.root)
        self.settings = load_settings()
        self.target = None
        self.recorder = None
        self.started_at = 0.0
        self.events = queue.Queue()
        self.last_file = None
        self.state = "idle"          # idle, recording, paused or saving
        self.parts = []              # finished pieces of a paused recording
        self.part_stop = None        # thread finishing the last piece
        self.elapsed = 0.0           # recorded seconds before the current piece
        self.mics, self.outputs = [], []
        self.root.report_callback_exception = self._on_error
        threading.excepthook = lambda a: self.events.put(
            ("error", "".join(traceback.format_exception(a.exc_type, a.exc_value,
                                                         a.exc_traceback))))

        self._build_toolbar()
        self._build_status_bar()
        # Settings panel, hidden until the gear is pressed.
        self.body = tk.Frame(self.root, bg=C["bg"], height=px(250))
        self.body.pack_propagate(False)
        self._build_sidebar(self.body)
        self.page_area = tk.Frame(self.body, bg=C["bg"])
        self.page_area.pack(side="left", fill="both", expand=True, padx=px(14), pady=px(4))
        self.pages = {
            "home": self._page_home(), "general": self._page_general(),
            "video": self._page_video(), "image": self._page_image(),
            "about": self._page_about(),
        }
        self.show_page("home")
        self.root.update_idletasks()
        self.body.config(width=self.toolbar.winfo_reqwidth())
        self._show_settings(self.settings["show_settings"])
        self.mic_button.set_on(self.settings["mic_on"] and bool(self.settings["mic"]))
        self.sound_button.set_on(self.settings["sound_on"] and loopback is not None)
        self.pick_screen(ask=False)
        self._apply_on_top()

        if not IS_WINDOWS:   # on Windows the global hotkeys cover this too
            self.root.bind("<F12>", lambda e: self.toggle_recording())
            self.root.bind("<F11>", lambda e: self.screenshot())
            self.root.bind("<F9>", lambda e: self.toggle_pause())
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        start_global_hotkeys(self.events, {VK_F12: "toggle", VK_F11: "screenshot",
                                           VK_F9: "pause"})
        self.refresh_devices()
        self.root.after(100, self._poll)

    # --- layout ------------------------------------------------------------

    def _build_toolbar(self):
        bar = self.toolbar = tk.Frame(self.root, bg=C["top"])
        bar.pack(fill="x")
        logo = tk.Frame(bar, bg=C["top"])
        logo.pack(side="left", padx=(px(10), px(8)))
        tk.Label(logo, text="EASY", font=(FONT, 9, "bold"), fg="white",
                 bg=C["top"]).pack(anchor="w")
        tk.Label(logo, text="RECORD", font=(FONT, 9, "bold"), fg=C["red"],
                 bg=C["top"]).pack(anchor="w")
        self.mode_buttons = {
            "screen": ModeButton(bar, "screen", "Screen", self.pick_screen),
            "window": ModeButton(bar, "window", "Window", self.pick_window),
            "region": ModeButton(bar, "area", "Area", self.pick_box),
        }
        for b in self.mode_buttons.values():
            b.pack(side="left", padx=(0, 1))
        IconButton(bar, "gear", self.toggle_settings, glyph="\u2699").pack(
            side="right", padx=(0, px(6)))
        IconButton(bar, "folder", self.open_folder, glyph="\U0001F4C2").pack(side="right")
        IconButton(bar, "camera", self.screenshot).pack(side="right")
        self.mic_button = SoundButton(bar, "mic", self.toggle_mic, self.mic_menu)
        self.mic_button.pack(side="right", padx=(0, px(2)))
        self.sound_button = SoundButton(bar, "speaker", self.toggle_sound, self.sound_menu)
        self.sound_button.pack(side="right", padx=(0, px(2)))
        self.pause_button = IconButton(bar, "pause", self.toggle_pause)
        self.pause_button.set_enabled(False)
        self.pause_button.pack(side="right", padx=(0, px(4)))
        self.rec_button = RecButton(bar, self.toggle_recording)
        self.rec_button.pack(side="right", padx=px(4), pady=px(3))
        self.timer = tk.Label(bar, text="00:00:00", font=("Consolas", 12),
                              fg=C["dim"], bg=C["top"])
        self.timer.pack(side="right", padx=(px(14), px(6)))

    def refresh_devices(self):
        """Look up mics and speakers without freezing the window."""
        def work():
            return list_microphones(find_ffmpeg()), self._outputs()

        def done(result):
            self.mics, self.outputs = result
            if self.state == "idle":
                self._show_target()
        self._background(work, done)

    def toggle_mic(self):
        if self.settings["mic_on"]:
            self._set_mic(False)
            return
        if not self.settings["mic"]:
            if not self.mics:
                self.refresh_devices()
                messagebox.showinfo("EasyRecord", "No microphone found.")
                return
            self.settings["mic"] = self.mics[0]
        self._set_mic(True)

    def mic_menu(self):
        mics = self.mics
        self.refresh_devices()
        menu = tk.Menu(self.root, tearoff=False, font=(FONT, 9), bg=C["field"],
                       fg=C["text"], activebackground=C["blue"],
                       activeforeground="white", bd=0)
        menu.add_command(label="\u2715  Microphone off", command=lambda: self._set_mic(False))
        menu.add_separator()
        if not mics:
            menu.add_command(label="(No microphone found)", state="disabled")
        for name in mics:
            mark = "\u2713  " if self.settings["mic_on"] and name == self.settings["mic"] else "     "
            menu.add_command(label=mark + name,
                             command=lambda n=name: self._set_mic(True, n))
        b = self.mic_button
        menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())

    def _set_mic(self, on, name=None):
        if name:
            self.settings["mic"] = name
        self.settings["mic_on"] = on
        save_settings(self.settings)
        self.mic_button.set_on(on)
        if self.state != "idle":
            self.status.config(text="Mic change applies to the next recording.",
                               fg=C["text"])
        else:
            self._show_target()

    def toggle_sound(self):
        self._set_sound(not self.settings["sound_on"])

    def _outputs(self):
        try:
            return loopback.list_outputs() if loopback else []
        except Exception:
            return []

    def sound_menu(self):
        outputs = self.outputs
        self.refresh_devices()
        default = next((name for _, name, d in outputs if d), "")
        menu = tk.Menu(self.root, tearoff=False, font=(FONT, 9), bg=C["field"],
                       fg=C["text"], activebackground=C["blue"],
                       activeforeground="white", bd=0)
        menu.add_command(label="\u2715  Computer sound off",
                         command=lambda: self._set_sound(False))
        menu.add_separator()
        on, chosen = self.settings["sound_on"], self.settings["sound"]

        def mark(this):
            return "\u2713  " if on and this == chosen else "     "
        menu.add_command(label=mark("") + f"Windows default ({default or 'none'})",
                         command=lambda: self._set_sound(True, "", ""))
        for dev_id, name, _ in outputs:
            menu.add_command(label=mark(dev_id) + name,
                             command=lambda i=dev_id, n=name: self._set_sound(True, i, n))
        b = self.sound_button
        menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())

    def _set_sound(self, on, dev_id=None, name=None):
        if dev_id is not None:
            self.settings["sound"], self.settings["sound_name"] = dev_id, name
        self.settings["sound_on"] = on
        save_settings(self.settings)
        self.sound_button.set_on(on)
        if self.state != "idle":
            self.status.config(text="Sound change applies to the next recording.",
                               fg=C["text"])
        else:
            self._show_target()

    def sound_label(self):
        """Name of the device computer sound is recorded from."""
        if self.settings["sound"]:
            return self.settings["sound_name"]
        default = next((name for _, name, d in self.outputs if d), "")
        return f"Default ({default})" if default else "Default"

    def toggle_settings(self):
        self._show_settings(not self.body.winfo_ismapped())

    def _show_settings(self, on):
        if on:
            self.body.pack(fill="both", expand=True)
        else:
            self.body.pack_forget()
        self.settings["show_settings"] = on
        save_settings(self.settings)

    def _build_status_bar(self):
        bar = tk.Frame(self.root, bg=C["status"])
        bar.pack(fill="x")
        icon = tk.Canvas(bar, width=px(12), height=px(12), bg=C["status"], highlightthickness=0)
        icon.create_rectangle(px(1), px(1), px(11), px(11), outline=C["text"], dash=(2, 2))
        icon.pack(side="left", padx=(px(10), px(5)), pady=px(3))
        self.status = tk.Label(bar, text="", font=(FONT, 8), fg=C["text"], width=1,
                               bg=C["status"], anchor="w")
        self.status.pack(side="left", fill="x", expand=True)

    def _build_sidebar(self, body):
        side = tk.Frame(body, bg=C["side"], width=px(96))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        self.side_buttons = {}
        for key, icon, label in PAGES:
            b = tk.Label(side, text=f"{icon}  {label}", font=(FONT, 9),
                         fg=C["dim"], bg=C["side"], anchor="w", cursor="hand2",
                         padx=px(10), pady=px(6))
            b.pack(fill="x")
            b.bind("<Button-1>", lambda e, k=key: self.show_page(k))
            self.side_buttons[key] = b

    def show_page(self, key):
        for k, page in self.pages.items():
            page.pack_forget()
            self.side_buttons[k].config(fg=C["dim"], font=(FONT, 9))
        self.pages[key].pack(fill="both", expand=True)
        self.side_buttons[key].config(fg="white", font=(FONT, 9, "bold"))

    # --- page helpers ------------------------------------------------------

    def _page(self):
        return tk.Frame(self.page_area, bg=C["bg"])

    def _section(self, page, title):
        tk.Label(page, text=title, font=(FONT, 10, "bold"), fg="white",
                 bg=C["bg"], anchor="w").pack(fill="x", pady=(px(5), px(1)))
        tk.Frame(page, bg=C["line"], height=1).pack(fill="x", pady=(0, px(3)))

    def _text(self, page, text, dim=False):
        lbl = tk.Label(page, text=text, font=(FONT, 9), bg=C["bg"], anchor="w",
                       justify="left", fg=C["dim"] if dim else C["text"])
        lbl.pack(fill="x", pady=px(1))
        return lbl

    def _choice(self, page, label, options, key):
        """A row of buttons where one is picked, e.g. 24 / 30 / 60."""
        row = tk.Frame(page, bg=C["bg"])
        row.pack(fill="x", pady=px(3))
        tk.Label(row, text=label, font=(FONT, 9), fg=C["text"], bg=C["bg"],
                 width=11, anchor="w").pack(side="left")
        buttons = {}

        def pick(value):
            self.settings[key] = value
            save_settings(self.settings)
            for v, b in buttons.items():
                b.config(bg=C["blue"] if v == value else C["field"])
        for value, text in options:
            b = tk.Button(row, text=text, relief="flat", bd=0, font=(FONT, 9),
                          highlightthickness=0,
                          fg=C["text"], activebackground=C["blue_hi"],
                          activeforeground="white", padx=px(10), pady=px(2),
                          cursor="hand2", command=lambda v=value: pick(v))
            b.pack(side="left", padx=(0, 2))
            buttons[value] = b
        pick(self.settings[key])
        return buttons

    def _checkbox(self, page, text, key, on_change=None):
        var = tk.BooleanVar(value=self.settings[key])

        def changed():
            self.settings[key] = var.get()
            save_settings(self.settings)
            if on_change:
                on_change()
        tk.Checkbutton(page, text=text, variable=var, command=changed,
                       font=(FONT, 9), fg=C["text"], bg=C["bg"],
                       activebackground=C["bg"], activeforeground="white",
                       selectcolor=C["field"], anchor="w", bd=0,
                       highlightthickness=0).pack(fill="x", pady=px(1))

    # --- pages -------------------------------------------------------------

    def _page_home(self):
        page = self._page()
        self._section(page, "Get started")
        self._text(page, "1.  Pick Screen, Window or Area.")
        self._text(page, "2.  Press REC, or F12 from anywhere, to start.")
        self._text(page, "3.  Press REC or F12 again to stop. It saves by itself.")
        self._text(page, "Pause / carry on: F9.   Screenshot: camera button or F11.", dim=True)
        self._section(page, "Last saved")
        row = tk.Frame(page, bg=C["bg"])
        row.pack(fill="x")
        self.last_label = tk.Label(row, text="Nothing yet.", font=(FONT, 9),
                                   fg=C["text"], bg=C["field"], anchor="w",
                                   padx=px(8), pady=px(4))
        self.last_label.pack(side="left", fill="x", expand=True)
        flat_button(row, "Play", self.play_last, width=6).pack(side="left", padx=(4, 0))
        flat_button(row, "Open", self.open_folder, width=6).pack(side="left", padx=(4, 0))
        return page

    def _page_general(self):
        page = self._page()
        self._section(page, "Options")
        self._text(page, "Save my files in")
        row = tk.Frame(page, bg=C["bg"])
        row.pack(fill="x", pady=(2, 8))
        self.folder_var = tk.StringVar(value=str(self.output_folder()))
        tk.Entry(row, textvariable=self.folder_var, font=(FONT, 9), relief="flat",
                 highlightthickness=0,
                 bg="white", fg="#222", state="readonly",
                 readonlybackground="white").pack(side="left", fill="x",
                                                  expand=True, ipady=px(3))
        flat_button(row, "...", self.choose_folder, width=4).pack(side="left", padx=(4, 0))
        flat_button(row, "Open", self.open_folder, width=6).pack(side="left", padx=(4, 0))
        self._checkbox(page, "Keep on top of other windows",
                       "always_on_top", self._apply_on_top)
        return page

    def _page_video(self):
        page = self._page()
        self._section(page, "Quality")
        row = tk.Frame(page, bg=C["bg"])
        row.pack(fill="x")
        self.quality = tk.IntVar(value=self.settings["quality"])
        self.quality_scale = Slider(row, self.quality, 0, 100, 5,
                                    command=self._quality_changed)
        self.quality_scale.pack(side="left")
        self.quality_label = tk.Label(row, font=(FONT, 10, "bold"), fg="white",
                                      bg=C["bg"], width=5)
        self.quality_label.pack(side="left", padx=8)
        self._text(page, "Higher = sharper video, but a bigger file.", dim=True)
        self._quality_changed()
        self._section(page, "Smoothness")
        self.fps_buttons = self._choice(page, "Frames / sec",
                                        [(24, "24"), (30, "30"), (60, "60")], "fps")
        self._section(page, "Hotkey")
        self._text(page, "Start / Stop recording:   F12")
        self._text(page, "Pause / Carry on:   F9")
        return page

    def _page_image(self):
        page = self._page()
        self._section(page, "Screenshots")
        self._choice(page, "Format", [("png", "PNG"), ("jpg", "JPG")], "image_format")
        self._text(page, "PNG = sharpest.  JPG = smaller file.", dim=True)
        self._section(page, "Hotkey")
        self._text(page, "Take a screenshot:   F11")
        return page

    def _page_about(self):
        page = self._page()
        self._section(page, "EasyRecord 1.0")
        self._text(page, "A free and simple screen recorder.")
        self._text(page, "Recording by FFmpeg (ffmpeg.org).",
                   dim=True)
        return page

    # --- picking -----------------------------------------------------------

    def pick_screen(self, ask=True):
        """One display: pick it. Several: show a menu under the Screen button."""
        displays = list_displays()
        if not displays:
            displays = [("Display 1", (0, 0, self.root.winfo_screenwidth(),
                                       self.root.winfo_screenheight()), True)]
        if len(displays) == 1 or not ask:
            name, rect, _ = next((d for d in displays if d[2]), displays[0])
            self.set_target(Target("screen", *rect))
            return
        menu = tk.Menu(self.root, tearoff=False, font=(FONT, 9), bg=C["field"],
                       fg=C["text"], activebackground=C["blue"],
                       activeforeground="white", bd=0)
        for name, rect, primary in displays:
            label = f"{name}  -  {rect[2]} x {rect[3]}" + ("  (main)" if primary else "")
            menu.add_command(label=label,
                             command=lambda r=rect: self.set_target(Target("screen", *r)))
        menu.add_separator()
        left = min(r[0] for _, r, _ in displays)
        top = min(r[1] for _, r, _ in displays)
        right = max(r[0] + r[2] for _, r, _ in displays)
        bottom = max(r[1] + r[3] for _, r, _ in displays)
        menu.add_command(label="All displays",
                         command=lambda: self.set_target(
                             Target("screen", left, top, right - left, bottom - top)))
        b = self.mode_buttons["screen"]
        menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())

    def pick_window(self):
        if not IS_WINDOWS:
            messagebox.showinfo("EasyRecord", "Picking a window only works on Windows.")
            return
        self.root.withdraw()  # hide so it is not in the way
        self.root.after(200, lambda: WindowPicker(self.root, self._picked))

    def pick_box(self):
        self.root.withdraw()  # hide so it is not in the way
        self.root.after(200, lambda: BoxSelector(self.root, self._picked))

    def _picked(self, target):
        self.root.deiconify()
        if target:
            self.set_target(target)

    def set_target(self, target):
        self.target = target
        for kind, b in self.mode_buttons.items():
            b.set_selected(kind == target.kind)
        self._show_target()

    def _show_target(self):
        t = self.target
        if t.kind == "window":
            text = f"Window - {t.window_title}"
        else:
            text = (f"{t.width}x{t.height} - ({t.x}, {t.y}), "
                    f"({t.x + t.width}, {t.y + t.height}) - "
                    + ("Full screen" if t.kind == "screen" else "Area"))
        if self.settings["sound_on"] and loopback:
            text += "   |   Sound: " + self.sound_label()
        if self.settings["mic_on"] and self.settings["mic"]:
            text += "   |   Mic: " + self.settings["mic"]
        self.status.config(text=text, fg=C["text"])

    # --- settings ----------------------------------------------------------

    def output_folder(self) -> Path:
        return videos_folder(self.settings["output_folder"])

    def choose_folder(self):
        from tkinter import filedialog
        self.root.attributes("-topmost", False)   # never hide the dialog behind us
        try:
            folder = filedialog.askdirectory(parent=self.root, mustexist=True,
                                             title="Save my videos in",
                                             initialdir=str(self.output_folder()))
        finally:
            self._apply_on_top()
        if not folder:
            return
        try:
            test = Path(folder) / ".easyrecord-test"
            test.write_text("ok")
            test.unlink()
        except OSError:
            messagebox.showerror("EasyRecord", "EasyRecord can't save files in:\n"
                                 f"{folder}\n\nPlease pick another folder.")
            return
        self.settings["output_folder"] = folder
        save_settings(self.settings)
        self.folder_var.set(str(self.output_folder()))

    def _apply_on_top(self):
        self.root.attributes("-topmost", bool(self.settings["always_on_top"]))

    def _quality_changed(self):
        q = self.quality.get()
        self.quality_label.config(text=f"{q}%")
        self.settings["quality"] = q
        save_settings(self.settings)

    # --- recording ---------------------------------------------------------

    def _ffmpeg_or_complain(self):
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            messagebox.showerror("EasyRecord", "Can't find ffmpeg.exe.\n"
                                 "Put it in the same folder as EasyRecord.")
        return ffmpeg

    def toggle_recording(self):
        if self.state in ("recording", "paused"):
            self.stop_recording()
        elif self.state == "idle":
            self.start_recording()
        # while saving, REC does nothing

    def start_recording(self):
        ffmpeg = self._ffmpeg_or_complain()
        if not ffmpeg:
            return
        if self.target.kind == "window":   # it may have moved since it was picked
            for title, (l, t, r, b) in windows_under_overlay():
                if title == self.target.window_title:
                    self.set_target(window_target(title, (l, t, r, b)))
                    break
        stamp = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
        # Settings are fixed for the whole recording, so paused parts match.
        self.take = dict(
            ffmpeg=ffmpeg, folder=self.output_folder(), stamp=stamp,
            mic=self.settings["mic"] if self.settings["mic_on"] else "",
            sound=self.settings["sound"] if self.settings["sound_on"] else None,
            quality=self.quality.get(), fps=self.settings["fps"])
        self.parts, self.elapsed, self.part_stop = [], 0.0, None
        self._start_part()
        self.state = "recording"
        self.rec_button.set_recording(True)
        self.pause_button.set_enabled(True)
        self.pause_button.set_kind("pause")
        self.timer.config(fg=C["red"])
        self._set_picking_enabled(False)

    def _start_part(self):
        t = self.take
        n = len(self.parts) + 1
        out = t["folder"] / f"EasyRecord {t['stamp']} part {n}.mp4"
        self.recorder = Recorder(t["ffmpeg"])
        self.recorder.start(self.target, t["quality"], t["fps"], t["folder"],
                            mic=t["mic"], sound=t["sound"], output=out)
        self.started_at = time.time()

    def _end_part(self):
        """Finish the current piece in the background."""
        rec, self.recorder = self.recorder, None
        self.elapsed += time.time() - self.started_at

        def finish():
            if rec.stop():
                self.parts.append(rec.output)
            else:
                self.events.put(("error", "Recording failed.\n\n" + rec.error_text()))
        self.part_stop = threading.Thread(target=finish, daemon=True)
        self.part_stop.start()

    def toggle_pause(self):
        if self.state == "recording":
            self._end_part()
            self.state = "paused"
            self.pause_button.set_kind("resume", color="#ffb74d")
            self.timer.config(fg="#ffb74d")
            self.status.config(text="Paused.  Press \u25b6 (or F9) to carry on.",
                               fg="#ffb74d")
        elif self.state == "paused":
            if self.part_stop and self.part_stop.is_alive():
                self.root.after(100, self.toggle_pause)   # last piece still saving
                return
            self._start_part()
            self.state = "recording"
            self.pause_button.set_kind("pause")
            self.timer.config(fg=C["red"])
            self._show_target()

    def stop_recording(self):
        if self.state == "recording":
            self._end_part()
        elif self.state != "paused":
            return
        self.state = "saving"
        self.rec_button.set_recording(False)
        self.pause_button.set_kind("pause")
        self.pause_button.set_enabled(False)
        self.timer.config(fg=C["dim"])
        self.status.config(text="Saving your video...", fg=C["text"])
        t, stopping = self.take, self.part_stop
        final = t["folder"] / f"EasyRecord {t['stamp']}.mp4"

        def work():
            if stopping:
                stopping.join()
            if not self.parts:
                return None
            return final if join_parts(t["ffmpeg"], list(self.parts), final) else None

        def done(path):
            self.state = "idle"
            self.timer.config(text="00:00:00")
            self._set_picking_enabled(True)
            if path:
                self._saved(path)
            else:
                self._show_target()
                if self.parts:
                    messagebox.showerror("EasyRecord", "Couldn't join the paused parts.\n"
                                         "They are saved separately in your folder.")
            if getattr(self, "quitting", False):
                self.root.destroy()
        self._background(work, done)

    def screenshot(self):
        ffmpeg = self._ffmpeg_or_complain()
        if not ffmpeg:
            return
        stamp = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
        path = self.output_folder() / f"EasyRecord {stamp}.{self.settings['image_format']}"
        hide = self.state == "idle"
        if hide:  # keep our own window out of the picture
            self.root.withdraw()
        target = self.target

        def done(ok):
            if hide:
                self.root.deiconify()
            if ok:
                self._saved(path)
            else:
                messagebox.showerror("EasyRecord", "Screenshot failed.")
        self.root.after(250, lambda: self._background(
            lambda: take_screenshot(ffmpeg, target, path), done))

    def _background(self, work, done):
        """Run work() on another thread, then done(result) back on this one."""
        def run():
            try:
                self.events.put(("call", done, work()))
            except Exception:
                self.events.put(("error", traceback.format_exc()))
        threading.Thread(target=run, daemon=True).start()

    def _on_error(self, *exc):
        self.events.put(("error", "".join(traceback.format_exception(*exc))))

    def _show_error(self, details):
        log = Path.home() / ".easyrecord-errors.log"
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"--- {datetime.now()}\n{details}\n")
        except OSError:
            pass
        if details.startswith("Recording failed"):
            messagebox.showerror("EasyRecord", details)
        else:
            messagebox.showerror("EasyRecord", "Something went wrong, sorry.\n\n"
                                 + details.strip().splitlines()[-1]
                                 + f"\n\nDetails were saved to {log}")

    def _saved(self, path: Path):
        self.last_file = path
        self.last_label.config(text=path.name)
        self.status.config(text="Saved:  " + path.name, fg="#81c784")
        self.root.after(4000, self._show_target)

    def _set_picking_enabled(self, enabled):
        for b in self.mode_buttons.values():
            b.set_enabled(enabled)
        self.quality_scale.config(state="normal" if enabled else "disabled")

    def _poll(self):
        while not self.events.empty():
            ev = self.events.get()
            if ev == "toggle":
                self.toggle_recording()
            elif ev == "screenshot":
                self.screenshot()
            elif ev == "pause":
                self.toggle_pause()
            elif ev[0] == "call":
                ev[1](ev[2])
            elif ev[0] == "error":
                self._show_error(ev[1])
        if self.state in ("recording", "paused"):
            secs = self.elapsed
            if self.state == "recording":
                secs += time.time() - self.started_at
            secs = int(secs)
            self.timer.config(text=f"{secs // 3600:02d}:{secs // 60 % 60:02d}:{secs % 60:02d}")
        if self.state == "recording" and not self.recorder.is_recording:
            self.stop_recording()  # FFmpeg quit by itself (e.g. window closed)
        self.root.after(250, self._poll)

    # --- misc --------------------------------------------------------------

    def _open(self, path):
        if IS_WINDOWS:
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", path])

    def open_folder(self):
        self._open(str(self.output_folder()))

    def play_last(self):
        if self.last_file and self.last_file.exists():
            self._open(str(self.last_file))

    def quit(self):
        if self.state in ("recording", "paused"):
            self.quitting = True     # save the video first, then close
            self.stop_recording()
        elif self.state == "saving":
            self.quitting = True
        else:
            self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
