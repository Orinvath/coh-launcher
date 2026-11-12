#!/usr/bin/env python3
"""Child of tools/picture.py: runs the real launcher window on the hidden screen, opens Settings on the Add-ons tab,
saves the window picture, and writes the watch pictures. Usage: picture_window.py <out.png> <config folder>"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402

from launcher import app as app_mod  # noqa: E402
from launcher import gametime, watchface  # noqa: E402

READY_SECONDS = 30
SETTLE_MS = 2500


def main(out, config_dir):
    out = Path(out)
    app, window, bridge = app_mod.create(Path(config_dir), smoke=True)
    window.show()
    bridge.start()
    code = []

    def wait_for_page(attempt=0):
        window.page().runJavaScript("document.body && document.body.dataset.ready === '1'",
                                    lambda ready: open_settings() if ready else retry(attempt))

    def retry(attempt):
        if attempt > READY_SECONDS * 4:
            code.append(1)
            app.quit()
        else:
            QTimer.singleShot(250, lambda: wait_for_page(attempt + 1))

    def open_settings():
        bridge.dispatch("settings-open", "{}")
        bridge.dispatch("settings-tab", '{"tab": "addons"}')
        QTimer.singleShot(SETTLE_MS, save)

    def save():
        window.grab().save(str(out), "PNG")
        clock = gametime.game_clock(12 * 3600 + 15 * 60)
        night = gametime.game_clock(12 * 3600)
        watchface.render(watchface.Face(clock.digits, clock.ampm, clock.is_day)).save(str(out.with_name(out.stem + "-day.png")))
        watchface.render(watchface.Face(night.digits, night.ampm, night.is_day)).save(str(out.with_name(out.stem + "-night.png")))
        text = "WARNING: Zombie attack in Skyway City."
        watchface.render(watchface.Card("5:39 PM", text, 0.0)).save(str(out.with_name(out.stem + "-alert.png")))
        code.append(0)
        app.quit()

    QTimer.singleShot(0, wait_for_page)
    app.exec()
    return code[0] if code else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
