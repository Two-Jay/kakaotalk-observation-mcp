"""Windows adapter. Reads an explicitly configured, already-open chat window."""
from __future__ import annotations

import ctypes
import os
import sys
import time
from pathlib import Path
from ctypes import wintypes


def api():
    if os.name != "nt":
        raise RuntimeError("실제 카톡 창 수집은 Windows에서만 지원합니다.")
    sys.coinit_flags = 2  # STA is required for OLE clipboard preservation.
    try:
        import pythoncom
        import win32clipboard
        import win32con
        import win32gui
        import win32process
        from pywinauto import Desktop, keyboard
    except ImportError as exc:
        raise RuntimeError("먼저 python -m pip install -r requirements.txt 를 실행하세요.") from exc
    return pythoncom, win32clipboard, win32con, win32gui, win32process, Desktop, keyboard


def process_name(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        buf, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
        if not kernel.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return Path(buf.value).name.casefold()
    finally:
        kernel.CloseHandle(handle)


def kakao_windows(desktop):
    from pywinauto.controls.hwndwrapper import InvalidWindowHandle
    windows = []
    for w in desktop(backend="win32").windows():
        try:
            if process_name(w.process_id()) == "kakaotalk.exe":
                windows.append(w)
        except InvalidWindowHandle:
            # Tooltips and transient desktop windows may disappear during inventory.
            continue
    return windows


def inspect_windows():
    *_, desktop, keyboard = api()
    return [{"title": w.window_text(), "handle": w.handle,
             "controls": [{"class": c.class_name(), "handle": c.handle,
                           "visible": c.is_visible()}
                          for c in w.descendants()]}
            for w in kakao_windows(desktop)]


class GUIInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT)]


def assert_focus(window, control, gui, process):
    thread, pid = process.GetWindowThreadProcessId(control.handle)
    info = GUIInfo()
    info.cbSize = ctypes.sizeof(info)
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUIInfo)]
    user.GetGUIThreadInfo.restype = wintypes.BOOL
    if (gui.GetForegroundWindow() != window.handle or
            not user.GetGUIThreadInfo(thread, ctypes.byref(info)) or
            info.hwndFocus != control.handle or process_name(pid) != "kakaotalk.exe"):
        raise RuntimeError(f"focus_mismatch: 입력 키를 보내지 않았습니다. "
                           f"expected_window={window.handle} foreground={gui.GetForegroundWindow()} "
                           f"expected_control={control.handle} focused_control={info.hwndFocus}")


def capture(room):
    def trace(stage):
        if room.get("trace"):
            print(f"capture: {stage}", file=sys.stderr, flush=True)
    trace("start")
    com, clipboard, constants, gui, process, desktop, keyboard = api()
    windows = [w for w in kakao_windows(desktop) if w.window_text() == room["window_title"]]
    if len(windows) != 1:
        raise RuntimeError("지정 제목의 카톡 창이 정확히 하나여야 합니다. 방을 별도 창으로 열어 두세요.")
    window = windows[0]
    if not window.is_visible() or gui.IsIconic(window.handle):
        raise RuntimeError("카톡방 창이 최소화되었거나 보이지 않습니다.")
    if "edit" in room["history_class"].casefold():
        raise RuntimeError("입력창 클래스는 지원하지 않습니다.")
    controls = [c for c in window.descendants() if c.class_name() == room["history_class"]
                and c.is_visible()]
    if len(controls) != 1:
        raise RuntimeError("대화 목록 컨트롤이 정확히 하나여야 합니다. inspect 결과를 확인하세요.")
    control = controls[0]
    previous_foreground = gui.GetForegroundWindow()
    copied_sequence = None
    ole = ctypes.OleDLL("ole32")
    ole.OleInitialize.argtypes = [ctypes.c_void_p]
    ole.OleInitialize.restype = ctypes.c_long
    ole.OleUninitialize.argtypes = []
    ole.OleUninitialize.restype = None
    if ole.OleInitialize(None) < 0:
        raise RuntimeError("OLE 클립보드 초기화 실패")
    try:
        # OleGetClipboard retains IDataObject, including non-text clipboard formats.
        trace("clipboard_backup")
        backup = com.OleGetClipboard() if clipboard.CountClipboardFormats() else None
        try:
            trace("focus")
            window.set_focus()
            for attempt in range(3):
                control.set_keyboard_focus()
                time.sleep(0.15)
                try:
                    assert_focus(window, control, gui, process)
                    break
                except RuntimeError:
                    if gui.GetForegroundWindow() != window.handle or attempt == 2:
                        raise
            assert_focus(window, control, gui, process)
            for _ in range(room.get("history_pageups", 0)):
                assert_focus(window, control, gui, process)
                keyboard.send_keys("{PGUP}", pause=0.05)
                time.sleep(0.15)
            keyboard.send_keys("^a", pause=0.05)
            time.sleep(0.1)
            assert_focus(window, control, gui, process)
            sequence = clipboard.GetClipboardSequenceNumber()
            keyboard.send_keys("^c", pause=0.05)
            trace("copy_requested")
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if clipboard.GetClipboardSequenceNumber() != sequence:
                    copied_sequence = clipboard.GetClipboardSequenceNumber()
                    break
                time.sleep(0.05)
            if copied_sequence is None:
                raise RuntimeError("복사 결과가 갱신되지 않았습니다. 해당 카톡 버전의 목록 복사를 확인하세요.")
            text = None
            while time.monotonic() < deadline:
                try:
                    clipboard.OpenClipboard()
                    try:
                        if not clipboard.IsClipboardFormatAvailable(constants.CF_UNICODETEXT):
                            raise RuntimeError("복사 결과에 Unicode 텍스트가 없습니다.")
                        text = clipboard.GetClipboardData(constants.CF_UNICODETEXT)
                    finally:
                        clipboard.CloseClipboard()
                    break
                except OSError:
                    time.sleep(0.05)
            if not text:
                raise RuntimeError("클립보드가 비어 있거나 읽기 시간 초과입니다.")
            assert_focus(window, control, gui, process)
            trace("copy_read")
            return text
        finally:
            # Do not overwrite a clipboard changed by somebody else after our read.
            if copied_sequence is not None and clipboard.GetClipboardSequenceNumber() == copied_sequence:
                trace("clipboard_restore")
                com.OleSetClipboard(backup)
                if backup is not None:
                    com.OleFlushClipboard()
            if previous_foreground and gui.GetForegroundWindow() == window.handle:
                try:
                    gui.SetForegroundWindow(previous_foreground)
                except Exception:
                    pass
    finally:
        trace("ole_cleanup")
        ole.OleUninitialize()
