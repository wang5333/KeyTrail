"""Optional visible Windows tray controls; no startup registration or persistence."""
import ctypes
from ctypes import wintypes as w
import os
import threading

from .core import ROOT


class Tray:
    def __init__(self, show, stop, quit_app, language=lambda: "en"):
        self.show, self.stop_compute, self.quit_app = show, stop, quit_app
        self.language = language
        self.hwnd = None

    def start(self):
        if os.name == "nt":
            threading.Thread(target=self.loop, name="visible-windows-tray", daemon=True).start()

    def close(self):
        if self.hwnd:
            ctypes.windll.user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
            ctypes.windll.user32.PostMessageW(self.hwnd, 0x10, 0, 0)

    def loop(self):
        user, shell, kernel = ctypes.windll.user32, ctypes.windll.shell32, ctypes.windll.kernel32
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

        class WC(ctypes.Structure):
            _fields_ = [("style", w.UINT), ("proc", callback_type), ("cls", ctypes.c_int),
                        ("win", ctypes.c_int), ("instance", w.HINSTANCE), ("icon", w.HICON),
                        ("cursor", w.HANDLE), ("brush", w.HBRUSH), ("menu", w.LPCWSTR), ("name", w.LPCWSTR)]

        class GUID(ctypes.Structure):
            _fields_ = [("a", w.DWORD), ("b", w.WORD), ("c", w.WORD), ("d", ctypes.c_ubyte * 8)]

        class NID(ctypes.Structure):
            _fields_ = [("size", w.DWORD), ("hwnd", w.HWND), ("id", w.UINT), ("flags", w.UINT),
                        ("message", w.UINT), ("icon", w.HICON), ("tip", w.WCHAR * 128),
                        ("state", w.DWORD), ("mask", w.DWORD), ("info", w.WCHAR * 256),
                        ("timeout", w.UINT), ("title", w.WCHAR * 64), ("info_flags", w.DWORD),
                        ("guid", GUID), ("balloon", w.HICON)]

        kernel.GetModuleHandleW.restype = w.HMODULE
        user.CreateWindowExW.restype = w.HWND
        user.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, ctypes.c_void_p]
        user.DefWindowProcW.restype = ctypes.c_ssize_t
        user.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        user.LoadImageW.restype = w.HANDLE
        user.LoadImageW.argtypes = [w.HINSTANCE, w.LPCWSTR, w.UINT, ctypes.c_int, ctypes.c_int, w.UINT]
        user.CreatePopupMenu.restype = w.HMENU
        user.AppendMenuW.argtypes = [w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR]
        user.TrackPopupMenu.restype = w.UINT
        user.TrackPopupMenu.argtypes = [w.HMENU, w.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       w.HWND, ctypes.c_void_p]
        user.DestroyMenu.argtypes = [w.HMENU]
        user.SetForegroundWindow.argtypes = [w.HWND]
        user.DestroyWindow.argtypes = [w.HWND]
        user.GetMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]
        user.TranslateMessage.argtypes = [ctypes.POINTER(w.MSG)]
        user.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
        user.DispatchMessageW.restype = ctypes.c_ssize_t
        user.GetCursorPos.argtypes = [ctypes.POINTER(w.POINT)]
        shell.Shell_NotifyIconW.argtypes = [w.DWORD, ctypes.POINTER(NID)]
        nid = NID()

        @callback_type
        def procedure(hwnd, message, wp, lp):
            if message == 0x8001:
                if lp == 0x203:
                    self.show()
                elif lp == 0x205:
                    menu = user.CreatePopupMenu()
                    labels = {
                        "en": ("Open KeyTrail", "Stop and save", "Quit"),
                        "zh-CN": ("打开工作台", "停止并保存", "退出"),
                        "zh-TW": ("開啟工作台", "停止並儲存", "離開"),
                        "ja": ("ワークスペースを開く", "停止して保存", "終了"),
                        "es": ("Abrir KeyTrail", "Detener y guardar", "Salir"),
                    }.get(self.language(), ("Open KeyTrail", "Stop and save", "Quit"))
                    for command, label in zip((1001, 1002, 1003), labels):
                        user.AppendMenuW(menu, 0, command, label)
                    point = w.POINT()
                    user.GetCursorPos(ctypes.byref(point))
                    user.SetForegroundWindow(hwnd)
                    command = user.TrackPopupMenu(menu, 0x180, point.x, point.y, 0, hwnd, None)
                    user.DestroyMenu(menu)
                    if command == 1001:
                        self.show()
                    elif command == 1002:
                        self.stop_compute()
                    elif command == 1003:
                        threading.Thread(target=self.quit_app, daemon=True).start()
                return 0
            if message == 0x10:
                shell.Shell_NotifyIconW(2, ctypes.byref(nid))
                user.DestroyWindow(hwnd)
                return 0
            if message == 2:
                user.PostQuitMessage(0)
                return 0
            return user.DefWindowProcW(hwnd, message, wp, lp)

        self.procedure = procedure
        name = "Puzzle71Tray" + str(os.getpid())
        instance = kernel.GetModuleHandleW(None)
        wc = WC(0, procedure, 0, 0, instance, None, None, None, None, name)
        if not user.RegisterClassW(ctypes.byref(wc)):
            return
        self.hwnd = user.CreateWindowExW(0, name, "71 号工作台", 0, 0, 0, 0, 0, None, None, instance, None)
        nid.size, nid.hwnd, nid.id, nid.flags, nid.message = ctypes.sizeof(NID), self.hwnd, 1, 7, 0x8001
        nid.icon = user.LoadImageW(None, str(ROOT / "web/icon.ico"), 1, 32, 32, 0x10)
        nid.tip = "KeyTrail"
        shell.Shell_NotifyIconW(0, ctypes.byref(nid))
        message = w.MSG()
        while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user.TranslateMessage(ctypes.byref(message))
            user.DispatchMessageW(ctypes.byref(message))
