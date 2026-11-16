"""Read-only background accessibility probe. Never activates or types into a window."""
import argparse
import json
import sys
import time
from pathlib import Path

from windows_source import api, kakao_windows


def probe(room):
    *_, desktop, keyboard = api()
    import win32gui
    windows = [w for w in kakao_windows(desktop) if w.window_text() == room]
    if len(windows) != 1:
        raise RuntimeError("target_not_unique")
    target = windows[0]
    before = win32gui.GetForegroundWindow()
    histories = [c for c in target.descendants() if c.class_name() == "EVA_VH_ListControl_Dblclk"]
    results = []
    for history in histories:
        wrapper = desktop(backend="uia").window(handle=history.handle).wrapper_object()
        item = {"class": history.class_name(), "handle": history.handle, "patterns": {}}
        for label, read in (
                ("text", lambda: wrapper.iface_text.DocumentRange.GetText(-1)),
                ("value", lambda: wrapper.iface_value.CurrentValue),
                ("legacy_value", lambda: wrapper.legacy_properties().get("Value"))):
            try:
                value = read()
                item["patterns"][label] = {"supported": True, "characters": len(value or "")}
            except Exception as exc:
                item["patterns"][label] = {"supported": False, "error_type": type(exc).__name__}
        children = wrapper.descendants()
        item["accessible_descendants"] = len(children)
        item["named_descendants"] = sum(bool(c.element_info.name) for c in children)
        results.append(item)
    after = win32gui.GetForegroundWindow()
    return {"room": room, "at": time.time(), "foreground_before": before,
            "foreground_after": after, "foreground_unchanged": before == after,
            "history_controls": results, "actions": "accessibility_reads_only"}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--room", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = probe(args.room)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "background-probe.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
