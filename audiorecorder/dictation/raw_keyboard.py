"""Windows keyboard reports delivered outside the system's keyboard hook chain."""

import ctypes
import logging
import threading
from ctypes import wintypes

log = logging.getLogger(__name__)

HWND_MESSAGE = wintypes.HWND(-3)
RIDEV_REMOVE = 0x0001
RIDEV_INPUTSINK = 0x0100
WM_INPUT = 0x00FF
WM_QUIT = 0x0012
RID_INPUT = 0x10000003
RIM_TYPEKEYBOARD = 1
UINT_ERROR = 0xFFFFFFFF
ERROR_INSUFFICIENT_BUFFER = 122


class RawInputDevice(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND),
    ]


class RawInputHeader(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE), ("wParam", wintypes.WPARAM),
    ]


class RawKeyboard(ctypes.Structure):
    _fields_ = [
        ("MakeCode", wintypes.USHORT), ("Flags", wintypes.USHORT),
        ("Reserved", wintypes.USHORT), ("VKey", wintypes.USHORT),
        ("Message", wintypes.UINT), ("ExtraInformation", wintypes.ULONG),
    ]


class RawKeyboardInput(ctypes.Structure):
    _fields_ = [("header", RawInputHeader), ("keyboard", RawKeyboard)]


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.RegisterRawInputDevices.argtypes = [
    ctypes.POINTER(RawInputDevice), wintypes.UINT, wintypes.UINT,
]
user32.RegisterRawInputDevices.restype = wintypes.BOOL
user32.GetRegisteredRawInputDevices.argtypes = [
    ctypes.POINTER(RawInputDevice), ctypes.POINTER(wintypes.UINT), wintypes.UINT,
]
user32.GetRegisteredRawInputDevices.restype = wintypes.UINT
user32.GetRawInputData.argtypes = [
    wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID,
    ctypes.POINTER(wintypes.UINT), wintypes.UINT,
]
user32.GetRawInputData.restype = wintypes.UINT
user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
]
user32.GetMessageW.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t
user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


def _open_window():
    window = user32.CreateWindowExW(
        0, "STATIC", None, 0, 0, 0, 0, 0, HWND_MESSAGE, None, None, None,
    )
    if not window:
        raise ctypes.WinError(ctypes.get_last_error())
    return window


def _register(window, flags):
    device = RawInputDevice(1, 6, flags, window)
    if not user32.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(device)):
        raise ctypes.WinError(ctypes.get_last_error())


def _keyboard_registration():
    number = wintypes.UINT()
    result = user32.GetRegisteredRawInputDevices(
        None, ctypes.byref(number), ctypes.sizeof(RawInputDevice),
    )
    if result == UINT_ERROR and ctypes.get_last_error() != ERROR_INSUFFICIENT_BUFFER:
        raise ctypes.WinError(ctypes.get_last_error())
    if number.value == 0:
        return None
    devices = (RawInputDevice * number.value)()
    result = user32.GetRegisteredRawInputDevices(
        devices, ctypes.byref(number), ctypes.sizeof(RawInputDevice),
    )
    if result == UINT_ERROR:
        raise ctypes.WinError(ctypes.get_last_error())
    return next((device for device in devices[:result]
                 if (device.usUsagePage, device.usUsage) == (1, 6)), None)


class RawKeyboardListener:
    """Keyboard reports from Raw Input on a thread of this process."""

    _on_key: object
    _on_error: object
    _thread: threading.Thread | None
    _thread_id: int
    _ready: threading.Event
    _stopping: threading.Event
    _error: Exception | None

    def __init__(self, on_key, on_error=None):
        self._on_key = on_key
        self._on_error = on_error
        self._thread = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._stopping = threading.Event()
        self._error = None

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("Raw keyboard input is already running")
        self._ready.clear()
        self._stopping.clear()
        self._error = None
        self._thread = threading.Thread(target=self._run, name="raw-keyboard", daemon=True)
        self._thread.start()
        if not self._ready.wait(3):
            self.stop()
            raise RuntimeError("Raw keyboard input did not start")
        if self._error is not None:
            self.stop()
            raise self._error

    def _run(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        window = None
        registered = False
        try:
            window = _open_window()
            if self._stopping.is_set():
                return
            _register(window, RIDEV_INPUTSINK)
            registered = True
            registration = _keyboard_registration()
            if (registration is None or registration.hwndTarget != window
                    or not registration.dwFlags & RIDEV_INPUTSINK):
                raise RuntimeError(
                    "Raw keyboard input registration does not target the listener window"
                )
            self._ready.set()
            self._listen()
        except Exception as error:
            self._error = error
            if self._ready.is_set():
                log.exception("Raw keyboard input stopped; the dictation hotkey no longer responds")
                if self._on_error is not None:
                    try:
                        self._on_error()
                    except Exception:
                        log.exception("Could not release dictation after raw keyboard input failed")
        finally:
            try:
                if registered:
                    registration = _keyboard_registration()
                    # Another library may have replaced this process-wide registration.
                    if registration is not None and registration.hwndTarget == window:
                        _register(None, RIDEV_REMOVE)
            except Exception:
                log.exception("Could not unregister raw keyboard input")
            finally:
                if window and not user32.DestroyWindow(window):
                    log.error("Could not destroy raw keyboard window: %s",
                              ctypes.WinError(ctypes.get_last_error()))
                self._thread_id = 0
                self._ready.set()

    def _listen(self):
        message, report, size = wintypes.MSG(), RawKeyboardInput(), wintypes.UINT()
        while not self._stopping.is_set():
            result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
            if result == -1:
                raise ctypes.WinError(ctypes.get_last_error())
            if result == 0:
                return
            try:
                if message.message == WM_INPUT and not self._stopping.is_set():
                    size.value = ctypes.sizeof(report)
                    if user32.GetRawInputData(
                        message.lParam, RID_INPUT, ctypes.byref(report), ctypes.byref(size),
                        ctypes.sizeof(RawInputHeader),
                    ) == UINT_ERROR:
                        raise ctypes.WinError(ctypes.get_last_error())
                    if report.header.dwType != RIM_TYPEKEYBOARD:
                        raise ValueError(f"Unregistered raw input type {report.header.dwType}")
                    self._on_key(report.keyboard.VKey, report.keyboard.Flags)
            finally:
                # STATIC's default procedure releases foreground WM_INPUT resources.
                user32.DispatchMessageW(ctypes.byref(message))

    def stop(self):
        self._stopping.set()
        if self._thread is None:
            return
        if self._thread_id and self._thread.is_alive():
            # Startup may not have created its queue yet; the event also cancels startup.
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._thread.join(2)
        if self._thread.is_alive():
            raise RuntimeError("Raw keyboard input did not stop")
        self._thread = None
