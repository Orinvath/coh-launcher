"""The real launcher window on the hidden screen: the Settings tabs, the Add-ons tab, scrolling, the toast order and the key box."""

import json
import time
import unittest

import harness
from harness import fresh_dir, pump, wait_until, write_config

from launcher import app as app_mod

APP = WINDOW = BRIDGE = BASE = BUS = None


def setUpModule():
    global APP, WINDOW, BRIDGE, BASE
    global BUS
    BASE = fresh_dir("ui")
    BUS = harness.PrivateBus(BASE)
    root = harness.make_game(BASE)
    proton = BASE / "proton"
    proton.write_bytes(b"")
    prefix = BASE / "prefix"
    (prefix / "pfx/drive_c").mkdir(parents=True)
    write_config(BASE / "cfg", gameRoot=str(root), protonPath=str(proton), compatPrefix=str(prefix), showWatch=False)
    APP, WINDOW, BRIDGE = app_mod.create(BASE / "cfg", smoke=True)
    WINDOW.resize(1635, 962)
    WINDOW.show()
    BRIDGE.start()
    assert wait_until(lambda: js("document.body.dataset.ready === '1'"), 30)


def tearDownModule():
    BRIDGE.shutdown()
    WINDOW.close()
    BUS.stop()


def jsj(code):
    """Run code that returns a value and get it back as Python data (through JSON, so lists and objects survive)."""
    return json.loads(js(f"JSON.stringify({code})"))


def js(code, seconds=5):
    box = []
    WINDOW.page().runJavaScript(code, box.append)
    assert wait_until(lambda: box, seconds), code
    return box[0]


def click(selector):
    js(f"document.querySelector({json.dumps(selector)}).click()")
    pump(0.15)


def text(selector):
    return js(f"(document.querySelector({json.dumps(selector)}) || {{}}).textContent")


def shown(selector):
    return js(f"(function(e){{ return !!e && !e.hidden && e.offsetParent !== null; }})(document.querySelector({json.dumps(selector)}))")


class UiCase(unittest.TestCase):
    def setUp(self):
        BRIDGE.dispatch("settings-cancel", "{}")
        BRIDGE._watch_error = None
        BRIDGE._notice = None
        BRIDGE._changed()
        pump(0.2)

    def open_settings(self, tab="addons"):
        click("#gear")
        wait_until(lambda: shown("#settings"))
        click(f"#tab-{tab}")
        pump(0.3)


class TestTabs(UiCase):
    def test_two_tabs_general_first_with_todays_settings(self):
        self.open_settings("general")
        self.assertEqual(text("#tab-general"), "General")
        self.assertEqual(text("#tab-addons"), "Add-ons")
        self.assertTrue(shown("#f-gameRoot") and shown("#f-protonPath") and shown("#f-compatPrefix"))
        self.assertTrue(shown("#f-skinId") and shown("#f-scale"))
        self.assertFalse(shown("#panel-addons"))

    def test_add_ons_tab_has_the_game_watch_section_with_both_switches_on(self):
        self.open_settings()
        self.assertEqual(text("#panel-addons .section-title"), "Game watch")
        self.assertEqual(text("#panel-addons .help"), "Turns the watch on when you press Play.")
        self.assertTrue(js("document.getElementById('f-startAutomatically').checked"))
        self.assertTrue(shown("#f-showWatch") and shown("#f-alertChime") and shown("#f-speakAlerts"))
        self.assertFalse(js("document.getElementById('f-speakAlerts').checked"))
        self.assertTrue(js("document.getElementById('f-alertChime').checked"))
        self.assertEqual(text("#key-line"), "Enter your Fish Audio key to load voices.")
        self.assertEqual(js("document.getElementById('f-voiceId').options.length"), 0)
        self.assertEqual(js("document.getElementById('f-key').value"), "")

    def test_save_and_cancel_work_for_every_tab(self):
        self.open_settings()
        js("var b=document.getElementById('f-startAutomatically'); b.checked=false;")
        click("#settings-cancel")
        self.assertFalse(shown("#settings"))
        self.open_settings()
        self.assertTrue(js("document.getElementById('f-startAutomatically').checked"))
        js("document.getElementById('f-startAutomatically').checked=false; document.getElementById('f-alertChime').checked=false;")
        click("#settings-save")
        wait_until(lambda: not shown("#settings"))
        saved = json.loads((BASE / "cfg/config.json").read_text())
        self.assertFalse(saved["startAutomatically"])
        self.assertFalse(saved["alertChime"])
        self.open_settings()
        self.assertFalse(js("document.getElementById('f-startAutomatically').checked"))
        js("document.getElementById('f-startAutomatically').checked=true; document.getElementById('f-alertChime').checked=true;")
        click("#settings-save")

    def test_only_the_tab_selected_shows_and_the_tab_survives_state_changes(self):
        self.open_settings()
        BRIDGE._changed()
        pump(0.2)
        self.assertTrue(shown("#panel-addons"))
        self.assertFalse(shown("#panel-general"))


class TestMessagesAndScrolling(UiCase):
    def force_messages(self):
        BRIDGE._watch_error = "The game watch could not open. No X11 display was found."
        BRIDGE._watch._messages.update(chime="Alert chime could not play: no sound device was found.", chatlog=True,
                                       speak="Last alert could not be spoken: Fish Audio did not accept your key. " * 3)
        BRIDGE._voices.line = {"kind": "loadError", "text": "Could not load voices: Fish Audio could not be reached. Check your internet connection. " * 2}
        BRIDGE._changed()
        pump(0.3)

    def tearDown(self):
        BRIDGE._watch._messages.update(chime=None, speak=None, chatlog=False)
        BRIDGE._voices.line = None

    def test_messages_show_in_order_with_the_tab_scrolling_and_buttons_in_view(self):
        self.open_settings()
        before = jsj("(function(){var r=document.getElementById('settings').getBoundingClientRect();return [r.width,r.height];})()")
        self.force_messages()
        items = jsj("Array.from(document.querySelectorAll('#watch-messages li')).map(function(e){return e.textContent})")
        self.assertEqual(items[0], "The game watch could not open. No X11 display was found.")
        self.assertEqual(items[1], "No game chat log found. Turn on chat logging in the game's options.")
        self.assertEqual(items[2], "Alert chime could not play: no sound device was found.")
        self.assertTrue(text("#speak-line").startswith("Last alert could not be spoken:"))
        after = jsj("(function(){var r=document.getElementById('settings').getBoundingClientRect();return [r.width,r.height];})()")
        self.assertEqual(before, after)
        body = jsj("(function(){var b=document.getElementById('settings-body');return [b.scrollHeight,b.clientHeight,b.scrollWidth,b.clientWidth];})()")
        self.assertGreater(body[0], body[1], "the tab should be taller than its space")
        self.assertEqual(body[2], body[3], "no sideways scrolling")
        tabs_top = js("document.querySelector('.tabs').getBoundingClientRect().top")
        save_top = js("document.getElementById('settings-save').getBoundingClientRect().top")
        js("document.getElementById('settings-body').scrollTop = 100000")
        pump(0.2)
        self.assertEqual(js("document.querySelector('.tabs').getBoundingClientRect().top"), tabs_top)
        self.assertEqual(js("document.getElementById('settings-save').getBoundingClientRect().top"), save_top)
        self.assertTrue(js("(function(){var e=document.getElementById('f-voiceId').getBoundingClientRect();"
                           "return e.bottom <= document.getElementById('settings-save').getBoundingClientRect().top + 1;})()"))
        self.assertGreater(js("document.getElementById('settings-body').scrollTop"), 0)

    def test_a_message_that_appears_does_not_move_the_controls_above_it(self):
        self.open_settings()
        top = js("document.getElementById('f-showWatch').getBoundingClientRect().top")
        self.force_messages()
        self.assertEqual(js("document.getElementById('f-showWatch').getBoundingClientRect().top"), top)

    def test_no_game_folder_message_while_show_watch_is_on(self):
        saved = BRIDGE._config
        self.addCleanup(setattr, BRIDGE, "_config", saved)
        BRIDGE._config = dict(saved, showWatch=True, gameRoot="")
        BRIDGE._changed()
        self.open_settings()
        items = jsj("Array.from(document.querySelectorAll('#watch-messages li')).map(function(e){return e.textContent})")
        self.assertIn("Set your game folder in the General tab.", items)


class TestToast(UiCase):
    WATCH = "The game watch could not open. No X11 display was found."

    def toast(self):
        return js("(function(){var t=document.getElementById('toast');return t.hidden ? '' : t.textContent;})()")

    def test_watch_message_then_short_notice_for_three_seconds_then_the_watch_message_again(self):
        BRIDGE._watch_error = self.WATCH
        BRIDGE._changed()
        pump(0.3)
        self.assertEqual(self.toast(), self.WATCH)
        BRIDGE._notice = {"tone": "info", "text": "Requested News in Chrome.", "serial": 1}
        BRIDGE._changed()
        pump(0.3)
        self.assertEqual(self.toast(), "Requested News in Chrome.")
        started = time.monotonic()
        self.assertTrue(wait_until(lambda: self.toast() == self.WATCH, 5))
        self.assertTrue(2.4 < time.monotonic() - started + 0.3 < 3.6)

    def test_an_error_notice_comes_first_and_the_watch_message_returns_when_it_goes(self):
        BRIDGE._watch_error = self.WATCH
        BRIDGE._notice = {"tone": "error", "text": "COH Capture Studio is not installed beside this launcher."}
        BRIDGE._changed()
        pump(0.3)
        self.assertEqual(self.toast(), "COH Capture Studio is not installed beside this launcher.")
        BRIDGE._notice = None
        BRIDGE._changed()
        pump(0.3)
        self.assertEqual(self.toast(), self.WATCH)

    def test_launch_requested_shows_first_then_the_watch_message(self):
        BRIDGE._watch_error = self.WATCH
        BRIDGE._notice = {"tone": "info", "text": "Launch requested"}
        BRIDGE._changed()
        pump(0.3)
        self.assertEqual(self.toast(), "Launch requested")
        self.assertTrue(wait_until(lambda: self.toast() == self.WATCH, 5))

    def test_home_clears_the_message_from_the_toast_and_the_section(self):
        BRIDGE._watch_error = self.WATCH
        BRIDGE._changed()
        pump(0.3)
        self.open_settings()
        self.assertEqual(text("#watch-messages li"), self.WATCH)
        click("#settings-cancel")
        click(".navlink")
        pump(0.3)
        self.assertEqual(self.toast(), "")
        self.open_settings()
        self.assertEqual(js("document.querySelectorAll('#watch-messages li').length"), 0)


class TestKeyBox(UiCase):
    def test_key_shows_as_dots_and_the_voice_pulldown_says_choose_a_voice(self):
        BRIDGE._voices.key_state = "stored"
        BRIDGE._voices.groups = [{"label": "My voices", "voices": [{"id": "a", "title": "Alpha"}]},
                                 {"label": "Public voices", "voices": [{"id": "b", "title": "Beta"}]}]
        BRIDGE._changed()
        self.open_settings()
        BRIDGE._voices.key_state = "stored"
        BRIDGE._voices.line = None
        BRIDGE._voices.groups = [{"label": "My voices", "voices": [{"id": "a", "title": "Alpha"}]},
                                 {"label": "Public voices", "voices": [{"id": "b", "title": "Beta"}]}]
        BRIDGE._changed()
        pump(0.3)
        self.assertEqual(js("document.getElementById('f-key').type"), "password")
        self.assertNotEqual(js("document.getElementById('f-key').value"), "")
        select = jsj("(function(s){return {text: s.options[s.selectedIndex].textContent, groups: Array.from(s.querySelectorAll('optgroup')).map(function(g){return g.label;}), opts: s.options.length};})(document.getElementById('f-voiceId'))")
        self.assertEqual(select["text"], "Choose a voice")
        self.assertEqual(select["groups"], ["My voices", "Public voices"])
        self.assertEqual(select["opts"], 3)
        self.assertFalse(shown("#key-line"))

    def test_the_voice_list_opens_inside_the_page_and_a_click_picks_a_voice(self):
        BRIDGE._changed()
        self.open_settings()
        BRIDGE._voices.key_state = "stored"
        BRIDGE._voices.line = None
        BRIDGE._voices.groups = [{"label": "My voices", "voices": [{"id": "a", "title": "Alpha"}]},
                                 {"label": "Public voices", "voices": [{"id": "b", "title": "Beta"}]}]
        BRIDGE._changed()
        pump(0.3)
        press = "document.getElementById('f-voiceId').dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true, button: 0}))"
        self.assertFalse(js(press), "the built-in popup must not open")
        pump(0.1)
        self.assertTrue(shown("#voice-list"))
        self.assertEqual(jsj("Array.from(document.querySelectorAll('#voice-list .voice-group')).map(function(e){return e.textContent;})"),
                         ["My voices", "Public voices"])
        self.assertEqual(jsj("Array.from(document.querySelectorAll('#voice-list .voice-item')).map(function(e){return e.textContent;})"),
                         ["Alpha", "Beta"])
        self.assertEqual(js("getComputedStyle(document.getElementById('voice-list')).scrollbarWidth"), "none")
        click("#voice-list .voice-item[data-value='b']")
        self.assertEqual(js("document.getElementById('f-voiceId').value"), "b")
        self.assertFalse(shown("#voice-list"))
        js(press)
        pump(0.1)
        self.assertTrue(shown("#voice-list"))
        js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))")
        pump(0.1)
        self.assertFalse(shown("#voice-list"))
        self.assertTrue(shown("#settings"), "Escape closes the list first, not Settings")

    def test_typing_in_the_key_box_asks_for_voices_after_a_pause(self):
        self.open_settings()
        calls = []
        original = BRIDGE._key_typed
        BRIDGE._handlers["key-typed"] = lambda payload: calls.append(payload["key"])
        try:
            js("(function(){var k=document.getElementById('f-key'); k.focus(); k.value='abc'; k.dispatchEvent(new Event('input'));})()")
            pump(0.4)
            self.assertEqual(calls, [])
            pump(1.2)
            self.assertEqual(calls, ["abc"])
        finally:
            BRIDGE._handlers["key-typed"] = original

    def test_the_key_box_cannot_be_typed_in_when_there_is_no_password_store_piece(self):
        BRIDGE._voices.key_state = "unavailable"
        self.open_settings()
        BRIDGE._voices.key_state = "unavailable"
        BRIDGE._changed()
        pump(0.3)
        self.assertTrue(js("document.getElementById('f-key').disabled"))
        BRIDGE._voices.key_state = "none"


class TestLook(UiCase):
    def test_the_page_uses_its_own_fonts_and_colours_not_the_desktop(self):
        self.open_settings()
        self.assertIn("Instrument Sans", js("getComputedStyle(document.querySelector('.switch-row label')).fontFamily"))
        self.assertEqual(js("getComputedStyle(document.getElementById('settings-save')).backgroundColor"), "rgb(255, 123, 41)")


if __name__ == "__main__":
    unittest.main()
