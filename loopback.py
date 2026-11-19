"""Records what the computer is playing (speakers/headphones) on Windows.

Uses WASAPI loopback through plain ctypes, so no extra packages are needed.
The sound is sent as raw PCM to a socket that FFmpeg reads.
"""

import ctypes
import socket
import threading
import time
import uuid
from ctypes import wintypes

ole32 = ctypes.windll.ole32

CLSCTX_ALL = 23
E_RENDER = 0           # playback devices
E_CONSOLE = 0
DEVICE_STATE_ACTIVE = 1
STREAMFLAGS_LOOPBACK = 0x00020000
BUFFER_SILENT = 0x2


class GUID(ctypes.Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]

    def __init__(self, text):
        super().__init__()
        ctypes.memmove(self.data, uuid.UUID(text).bytes_le, 16)


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort),
                ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("ptr", ctypes.c_void_p), ("pad", ctypes.c_void_p)]


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD),
                ("nSamplesPerSec", wintypes.DWORD), ("nAvgBytesPerSec", wintypes.DWORD),
                ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD),
                ("cbSize", wintypes.WORD)]


CLSID_MMDeviceEnumerator = GUID("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDeviceEnumerator = GUID("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IAudioClient = GUID("1CB9AD4C-DBFA-4C32-B178-C2F568A703B2")
IID_IAudioCaptureClient = GUID("C8ADBD64-E71E-48A0-A4DE-185C395CD317")
PKEY_FriendlyName = PROPERTYKEY(GUID("A45C254E-DF1C-4EFD-8020-67D146A850E0"), 14)


def _call(obj, index, *args):
    """Call method number `index` of a COM object (vtable order)."""
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    types = [ctypes.c_void_p]
    for a in args:
        plain = isinstance(a, ctypes._SimpleCData) and not isinstance(
            a, (ctypes.c_void_p, ctypes.c_wchar_p))
        types.append(type(a) if plain else ctypes.c_void_p)
    fn = ctypes.WINFUNCTYPE(ctypes.HRESULT, *types)(vtable[index])
    return fn(obj, *args)


def _release(obj):
    if obj:
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])(obj)


def _in_worker(fn, *args):
    """Run fn on a short-lived background thread and return its result.

    Windows audio needs COM in "multi-threaded" mode. Doing that on the app's
    main thread breaks Windows dialogs (the folder picker froze), so all
    audio setup happens on worker threads instead.
    """
    box = {}

    def run():
        try:
            box["result"] = fn(*args)
        except BaseException as e:      # handed back to the caller
            box["error"] = e
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(15)
    if "error" in box:
        raise box["error"]
    if "result" not in box:
        raise TimeoutError("Windows audio did not answer")
    return box["result"]


def _init_com():
    ole32.CoInitializeEx(None, 0)


def _enumerator():
    obj = ctypes.c_void_p()
    ole32.CoCreateInstance(ctypes.byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
                           ctypes.byref(IID_IMMDeviceEnumerator), ctypes.byref(obj))
    return obj


def _device_id(dev):
    p = ctypes.c_wchar_p()
    _call(dev, 5, ctypes.byref(p))
    text = p.value
    ole32.CoTaskMemFree(p)
    return text


def _device_name(dev):
    store = ctypes.c_void_p()
    _call(dev, 4, wintypes.DWORD(0), ctypes.byref(store))
    pv = PROPVARIANT()
    _call(store, 5, ctypes.byref(PKEY_FriendlyName), ctypes.byref(pv))
    name = ctypes.wstring_at(pv.ptr) if pv.ptr else "Unknown device"
    ole32.PropVariantClear(ctypes.byref(pv))
    _release(store)
    return name


def list_outputs():
    """[(device_id, name, is_default)] for every active speaker/headphone output."""
    return _in_worker(_list_outputs)


def _list_outputs():
    _init_com()
    result = []
    en = _enumerator()
    try:
        default_id = ""
        dev = ctypes.c_void_p()
        try:
            _call(en, 4, ctypes.c_int(E_RENDER), ctypes.c_int(E_CONSOLE), ctypes.byref(dev))
            default_id = _device_id(dev)
            _release(dev)
        except OSError:
            pass
        coll = ctypes.c_void_p()
        _call(en, 3, ctypes.c_int(E_RENDER), wintypes.DWORD(DEVICE_STATE_ACTIVE),
              ctypes.byref(coll))
        count = ctypes.c_uint()
        _call(coll, 3, ctypes.byref(count))
        for i in range(count.value):
            dev = ctypes.c_void_p()
            _call(coll, 4, ctypes.c_uint(i), ctypes.byref(dev))
            dev_id = _device_id(dev)
            result.append((dev_id, _device_name(dev), dev_id == default_id))
            _release(dev)
        _release(coll)
    finally:
        _release(en)
    return result


class LoopbackCapture:
    """Streams one output device's sound to a TCP address FFmpeg listens on.

    device_id "" means the Windows default output.
    Gaps where nothing plays are filled with silence so the sound stays in sync.
    """

    def __init__(self, device_id=""):
        self.device_id = device_id
        self.rate, self.channels, self.fmt = 48000, 2, "f32le"
        self._stop = threading.Event()
        self._thread = None
        self._client = None
        self._capture = None
        self._block = 8
        _in_worker(self._open)

    def _open(self):
        _init_com()
        en = _enumerator()
        dev = ctypes.c_void_p()
        try:
            if self.device_id:
                _call(en, 5, ctypes.c_wchar_p(self.device_id), ctypes.byref(dev))
            else:
                _call(en, 4, ctypes.c_int(E_RENDER), ctypes.c_int(E_CONSOLE),
                      ctypes.byref(dev))
            client = ctypes.c_void_p()
            _call(dev, 3, ctypes.byref(IID_IAudioClient), wintypes.DWORD(CLSCTX_ALL),
                  None, ctypes.byref(client))
        finally:
            _release(dev)
            _release(en)
        fmt_ptr = ctypes.POINTER(WAVEFORMATEX)()
        _call(client, 8, ctypes.byref(fmt_ptr))
        f = fmt_ptr.contents
        self.rate, self.channels = f.nSamplesPerSec, f.nChannels
        self._block = f.nBlockAlign
        bits = f.wBitsPerSample
        # Shared-mode mix formats are float32 in practice; 16-bit just in case.
        self.fmt = "f32le" if bits == 32 else ("s16le" if bits == 16 else "s24le")
        _call(client, 3, ctypes.c_int(0), wintypes.DWORD(STREAMFLAGS_LOOPBACK),
              ctypes.c_longlong(20_000_000), ctypes.c_longlong(0), fmt_ptr, None)
        ole32.CoTaskMemFree(fmt_ptr)
        capture = ctypes.c_void_p()
        _call(client, 14, ctypes.byref(IID_IAudioCaptureClient), ctypes.byref(capture))
        self._client, self._capture = client, capture

    def ffmpeg_args(self, port):
        # Tiny probe so FFmpeg starts without waiting for sound to arrive.
        return ["-thread_queue_size", "1024", "-probesize", "32",
                "-analyzeduration", "0", "-f", self.fmt, "-ar", str(self.rate),
                "-ac", str(self.channels), "-i", f"tcp://127.0.0.1:{port}?listen=1"]

    def start(self, port, ready=lambda: True, video_start=None):
        """ready() turns True once FFmpeg has started recording. video_start is
        the perf_counter() time of the video's first picture; the time between
        that and the sound starting becomes silence, so the two line up."""
        self._ready, self._video_start = ready, video_start
        self._thread = threading.Thread(target=self._run, args=(port,), daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _connect(self, port):
        deadline = time.time() + 10
        while not self._stop.is_set() and time.time() < deadline:
            try:
                s = socket.create_connection(("127.0.0.1", port), timeout=1)
                s.settimeout(2)
                return s
            except OSError:
                time.sleep(0.05)
        return None

    def _run(self, port):
        _init_com()
        sock = self._connect(port)
        if not sock:
            return
        client, capture = self._client, self._capture
        deadline = time.time() + 15
        while not self._ready() and time.time() < deadline and not self._stop.is_set():
            time.sleep(0.005)
        _call(client, 10)   # Start
        start = time.perf_counter()
        sent = 0            # frames sent so far
        if self._video_start is not None:
            lead = max(0, int((start - self._video_start) * self.rate))
            sock.sendall(bytes(lead * self._block))
        data = ctypes.POINTER(ctypes.c_ubyte)()
        frames, flags = ctypes.c_uint32(), wintypes.DWORD()
        packet = ctypes.c_uint32()
        try:
            while not self._stop.is_set():
                _call(capture, 5, ctypes.byref(packet))
                got = packet.value > 0
                while packet.value:
                    _call(capture, 3, ctypes.byref(data), ctypes.byref(frames),
                          ctypes.byref(flags), None, None)
                    n = frames.value * self._block
                    if flags.value & BUFFER_SILENT:
                        chunk = bytes(n)
                    else:
                        chunk = ctypes.string_at(data, n)
                    _call(capture, 4, frames)
                    sock.sendall(chunk)
                    sent += frames.value
                    _call(capture, 5, ctypes.byref(packet))
                # When nothing plays, Windows sends nothing: fill the gap with
                # silence so the sound keeps pace with the clock (and the video).
                behind = int((time.perf_counter() - start) * self.rate) - sent
                if not got and behind > self.rate // 10:
                    pad = behind - self.rate // 50
                    sock.sendall(bytes(pad * self._block))
                    sent += pad
                time.sleep(0.005)
        except OSError:
            pass
        finally:
            try:
                _call(client, 11)   # Stop
            except OSError:
                pass
            _release(capture)
            _release(client)
            sock.close()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
