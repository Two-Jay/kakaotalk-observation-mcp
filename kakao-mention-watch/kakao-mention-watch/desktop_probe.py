"""Standalone, read-only desktop inventory used to calibrate the collector."""
import argparse
import json
import time
from pathlib import Path

from windows_source import api, kakao_windows


def inspect(output=None):
    *_, desktop, keyboard = api()
    result = []
    for w in kakao_windows(desktop):
        if not w.window_text() or w.class_name() != "EVA_Window_Dblclk":
            continue
        rect = w.rectangle()
        item = {"handle": w.handle, "title": w.window_text(), "class": w.class_name(),
                "rect": [rect.left, rect.top, rect.right, rect.bottom], "controls": []}
        for c in w.descendants():
            r = c.rectangle()
            item["controls"].append({"handle": c.handle, "parent": c.parent().handle,
                                     "class": c.class_name(), "visible": c.is_visible(),
                                     "rect": [r.left, r.top, r.right, r.bottom]})
        if output and w.window_text() in ("카카오톡", "KakaoTalk"):
            w.set_focus()
            time.sleep(0.5)
            folder = Path(output).resolve()
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"main-{w.handle}.png"
            w.capture_as_image().save(path)
            item["screenshot"] = str(path)
        result.append(item)
    return {"windows": result}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--output")
    args = p.parse_args()
    print(json.dumps(inspect(args.output), ensure_ascii=False))
