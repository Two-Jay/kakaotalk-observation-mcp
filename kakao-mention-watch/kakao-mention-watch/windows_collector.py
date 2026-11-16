"""KakaoTalk desktop collector; runs independently of Codex and any vision model.

Only observed native controls are used. No message composer input, internal DB,
process memory access, or Kakao network endpoints.
"""
from contextlib import contextmanager
from pathlib import Path
import time

from windows_source import api, assert_focus, capture, kakao_windows


@contextmanager
def desktop_lock():
    import win32api
    import win32event
    import win32con
    import win32service
    # A locked or non-interactive desktop is not a usable collection environment.
    desk = win32service.OpenInputDesktop(0, False, win32con.DESKTOP_READOBJECTS)
    try:
        if win32service.GetUserObjectInformation(desk, win32con.UOI_NAME) != "Default":
            raise RuntimeError("desktop_locked: 로그인된 기본 데스크톱이 필요합니다.")
    finally:
        del desk  # PyHDESK owns and closes its handle on release.
    mutex = win32event.CreateMutex(None, False, "Local\\KakaoObservationCollectorDemo")
    acquired = False
    try:
        acquired = win32event.WaitForSingleObject(mutex, 0) in (0, 0x80)
        if not acquired:
            raise RuntimeError("collector_busy: 다른 수집기가 실행 중입니다.")
        yield
    finally:
        if acquired:
            win32event.ReleaseMutex(mutex)
        win32api.CloseHandle(mutex)


class WindowsCollector:
    history_class = "EVA_VH_ListControl_Dblclk"

    def __init__(self, room, output):
        if not room.strip() or len(room) > 200 or any(c in room for c in "\r\n\t"):
            raise ValueError("invalid_room: 정확한 방 제목을 지정하세요.")
        self.room = room
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.com, self.clipboard, self.constants, self.gui, self.process, self.desktop, self.keyboard = api()

    def windows(self):
        return [w for w in kakao_windows(self.desktop)
                if w.window_text() and w.class_name() == "EVA_Window_Dblclk"]

    def inspect(self):
        windows = self.windows()
        return {"status": "ok", "kakao_window_count": len(windows),
                "main": [{"handle": w.handle, "title": w.window_text()} for w in windows
                         if w.window_text() in ("카카오톡", "KakaoTalk")],
                "target": [{"handle": w.handle, "title": w.window_text()} for w in windows
                           if w.window_text() == self.room]}

    def target(self):
        matches = [w for w in self.windows() if w.window_text() == self.room]
        if len(matches) != 1:
            raise RuntimeError("target_not_unique: 정확한 제목의 방 창이 하나여야 합니다.")
        return matches[0]

    def screenshot(self, window, name):
        if self.gui.GetForegroundWindow() != window.handle:
            raise RuntimeError("focus_changed: 스크린샷 대상 창이 활성 상태가 아닙니다.")
        path = self.output / name
        window.capture_as_image().save(path)
        return str(path)

    def search(self):
        mains = [w for w in self.windows() if w.window_text() in ("카카오톡", "KakaoTalk")]
        if len(mains) != 1:
            raise RuntimeError("main_not_unique: 로그인된 카카오톡 메인 창이 필요합니다.")
        main = mains[0]
        if self.gui.IsIconic(main.handle):
            main.restore()
        main.set_focus()
        if not any(c.class_name() == "Edit" and c.is_visible()
                   and c.rectangle().width() > 100 for c in main.descendants()):
            if self.gui.GetForegroundWindow() != main.handle:
                raise RuntimeError("focus_changed: 메인 창 활성화 실패")
            self.keyboard.send_keys("^f", pause=0.1)
            time.sleep(0.4)
        # Calibrated against the observed chat-list search. Reject login and chat
        # composer controls; never type into an arbitrary focused Edit.
        edits = [c for c in main.descendants() if c.class_name() == "Edit" and c.is_visible()
                 and c.rectangle().width() > 100 and c.rectangle().height() > 10
                 and c.parent().class_name() == "EVA_Window"]
        if len(edits) != 1:
            raise RuntimeError("search_not_ready: 메인 창 채팅 탭의 검색란을 먼저 열어 주세요.")
        edit = edits[0]
        lists = [c for c in edit.parent().children() if c.class_name() == self.history_class and c.is_visible()]
        if len(lists) != 1:
            raise RuntimeError("search_layout_changed: 검색 UI 구조를 확인할 수 없습니다.")
        edit.set_keyboard_focus()
        assert_focus(main, edit, self.gui, self.process)
        edit.set_edit_text(self.room)
        time.sleep(0.8)
        edit.set_keyboard_focus()
        time.sleep(0.1)
        assert_focus(main, edit, self.gui, self.process)
        if edit.window_text() != self.room:
            raise RuntimeError("search_failed: 검색어가 일치하지 않습니다.")
        return main, edit

    def search_preview(self):
        main, _ = self.search()
        return {"status": "ok", "query": self.room,
                "screenshot": self.screenshot(main, "search-result.png")}

    def open_room(self):
        main, edit = self.search()
        assert_focus(main, edit, self.gui, self.process)
        # Enter is issued only to the validated MAIN search field, never a room.
        self.keyboard.send_keys("{ENTER}", pause=0.1)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            matches = [w for w in self.windows() if w.window_text() == self.room]
            if len(matches) > 1:
                raise RuntimeError("target_not_unique: 같은 제목의 창이 여러 개입니다.")
            if matches and self.gui.GetForegroundWindow() == matches[0].handle:
                return {"status": "ok", "room": self.room, "handle": matches[0].handle,
                        "opened_via": "main_search_enter"}
            time.sleep(0.2)
        raise RuntimeError("open_failed: 검색 후 지정 방의 활성화를 확인하지 못했습니다.")

    def read(self, history_pageups=0):
        window = self.target()
        if self.gui.IsIconic(window.handle):
            window.restore()
        # Existing adapter validates focus before every Ctrl+A/C and restores clipboard.
        return capture({"window_title": self.room, "history_class": self.history_class,
                        "history_pageups": history_pageups})
