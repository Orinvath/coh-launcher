"""Feature 3: the settings file, the starting values, the tabs' data and what Save and Cancel do."""

import json
import unittest

import harness
from harness import BridgeRig, fresh_dir

from launcher.linux import startpaths


def setUpModule():
    harness.app()


def make_home(base, game=False, prefix=False, protons=()):
    home = base / "home"
    home.mkdir(parents=True, exist_ok=True)
    if game:
        install = home / "Games/Homecoming/pfx/drive_c/Games/Homecoming"
        (install / "bin/win64").mkdir(parents=True)
        (install / "bin/win64/launchercli.exe").write_bytes(b"")
        (install / "settings/launcher/profiles").mkdir(parents=True)
    if prefix:
        (home / "Games/Homecoming/pfx/drive_c").mkdir(parents=True, exist_ok=True)
    for name in protons:
        folder = home / ".local/share/Steam/steamapps/common" / name
        folder.mkdir(parents=True)
        (folder / "proton").write_bytes(b"")
    return home


class TestStartingValues(unittest.TestCase):
    def proton(self, *names):
        home = make_home(fresh_dir("sp"), protons=names)
        found = startpaths.find_start_paths({"HOME": str(home)})["protonPath"]
        return found.split("/common/")[1].split("/")[0] if found else ""

    def test_no_usable_home_means_three_empty_paths(self):
        base = fresh_dir("sp")
        for env in ({}, {"HOME": ""}, {"HOME": "relative/dir"}, {"HOME": str(base / "missing")}):
            self.assertEqual(startpaths.find_start_paths(env), {"gameRoot": "", "protonPath": "", "compatPrefix": ""})

    def test_a_home_with_nothing_has_empty_paths(self):
        home = make_home(fresh_dir("sp"))
        self.assertEqual(startpaths.find_start_paths({"HOME": str(home)}),
                         {"gameRoot": "", "protonPath": "", "compatPrefix": ""})

    def test_install_prefix_and_newest_proton(self):
        home = make_home(fresh_dir("sp"), game=True, prefix=True, protons=("Proton 9.0", "Proton 10.0"))
        found = startpaths.find_start_paths({"HOME": str(home)})
        self.assertEqual(found["gameRoot"], str(home / "Games/Homecoming/pfx/drive_c/Games/Homecoming"))
        self.assertEqual(found["compatPrefix"], str(home / "Games/Homecoming"))
        self.assertEqual(found["protonPath"], str(home / ".local/share/Steam/steamapps/common/Proton 10.0/proton"))

    def test_proton_ranking(self):
        self.assertEqual(self.proton("Proton 9.0", "Proton - Experimental", "Proton Hotfix"), "Proton 9.0")
        self.assertEqual(self.proton("Proton 9.0 (Beta)", "Proton 10.0-1", "Proton 8.0"), "Proton 10.0-1")
        self.assertEqual(self.proton("Proton 10.0", "Proton 10.0.1"), "Proton 10.0.1")
        self.assertEqual(self.proton("Proton 10.0", "Proton 10.0-1"), "Proton 10.0-1")
        self.assertEqual(self.proton("Proton 10", "Proton 9.0"), "Proton 10")
        self.assertEqual(self.proton("Proton - Experimental"), "")

    def test_a_folder_without_a_proton_file_does_not_count(self):
        home = make_home(fresh_dir("sp"), protons=("Proton 9.0",))
        (home / ".local/share/Steam/steamapps/common/Proton 11.0").mkdir()
        self.assertIn("Proton 9.0", startpaths.find_start_paths({"HOME": str(home)})["protonPath"])


class TestSettingsFile(unittest.TestCase):
    def test_fresh_launcher_has_both_switches_on_and_nobody_elses_folders(self):
        rig = BridgeRig(config=False)
        self.addCleanup(rig.close)
        s = rig.state()["settings"]
        self.assertEqual((s["gameRoot"], s["protonPath"], s["compatPrefix"]), ("", "", ""))
        self.assertTrue(s["showWatch"])
        self.assertTrue(s["startAutomatically"])
        self.assertTrue(rig.state()["settingsOpen"])
        self.assertEqual(rig.state()["settingsTab"], "general")
        self.assertEqual(len(rig.state()["pathProblems"]), 3)

    def test_version_1_file_is_accepted_and_rewritten_as_2_on_save(self):
        rig = BridgeRig(config=False)
        old = {"version": 1, "gameRoot": str(rig.root), "protonPath": str(rig.proton),
               "compatPrefix": str(rig.prefix), "skinId": "homecoming", "scale": 1, "lastProfile": "live"}
        rig.config_dir.mkdir(parents=True, exist_ok=True)
        (rig.config_dir / "config.json").write_text(json.dumps(old))
        rig.close()
        rig = BridgeRig(config=False, base=rig.base)
        self.addCleanup(rig.close)
        self.assertEqual(rig.file()["version"], 1)
        state = rig.state()
        self.assertIsNone(state["notice"])
        self.assertTrue(state["settings"]["showWatch"] and state["settings"]["startAutomatically"])
        self.assertEqual(state["settings"]["gameRoot"], str(rig.root))
        rig.send("settings-open")
        rig.save(scale=1.25)
        saved = rig.file()
        self.assertEqual(saved["version"], 2)
        self.assertEqual((saved["gameRoot"], saved["protonPath"], saved["compatPrefix"]),
                         (str(rig.root), str(rig.proton), str(rig.prefix)))
        for key in ("showWatch", "startAutomatically", "watchSpot", "alertChime", "speakAlerts", "voiceId"):
            self.assertIn(key, saved)
        self.assertEqual(saved["scale"], 1.25)

    def test_broken_version_2_files_are_kept_aside(self):
        cases = {
            "wrong kind": lambda d: d.update(showWatch="yes"),
            "missing": lambda d: d.pop("alertChime"),
            "unknown": lambda d: d.update(extra=1),
            "bad spot": lambda d: d.update(watchSpot=[1]),
        }
        for name, change in cases.items():
            rig = BridgeRig(config=False)
            data = harness.write_config(rig.config_dir)
            change(data)
            (rig.config_dir / "config.json").write_text(json.dumps(data))
            rig.close()
            rig = BridgeRig(config=False, base=rig.base)
            self.assertTrue(list(rig.config_dir.glob("config.invalid-*.json")), name)
            self.assertIn("could not be read", rig.state()["notice"]["text"], name)
            rig.close()

    def test_key_is_never_in_the_file(self):
        rig = BridgeRig(store=True)
        self.addCleanup(rig.close)
        rig.send("settings-open")
        rig.save(key={"mode": "set", "value": "very-secret-key"})
        harness.wait_until(lambda: rig.bus.key(), 5)
        self.assertEqual(rig.bus.key(), "very-secret-key")
        self.assertNotIn("very-secret-key", (rig.config_dir / "config.json").read_text())


class TestSaveAndCancel(unittest.TestCase):
    def test_empty_paths_are_saved_then_settings_opens_again_on_general(self):
        rig = BridgeRig(config=False)
        self.addCleanup(rig.close)
        rig.send("settings-tab", tab="addons")
        state = rig.save(startAutomatically=False)
        saved = rig.file()
        self.assertFalse(saved["startAutomatically"])
        self.assertEqual((saved["gameRoot"], saved["protonPath"], saved["compatPrefix"]), ("", "", ""))
        self.assertTrue(state["settingsOpen"])
        self.assertEqual(state["settingsTab"], "general")
        self.assertFalse(state["settings"]["startAutomatically"])
        self.assertEqual(len(state["pathProblems"]), 3)
        self.assertEqual(state["pathProblems"][0]["message"], "Game installation: enter a full path.")

    def test_wrong_path_stops_save_and_cancel_keeps_the_old_value(self):
        rig = BridgeRig()
        self.addCleanup(rig.close)
        rig.send("settings-open")
        state = rig.save(gameRoot="Games/Homecoming", startAutomatically=False)
        self.assertEqual(state["settingsError"]["message"], "Game installation: the path must start with /.")
        self.assertTrue(rig.file()["startAutomatically"])
        rig.send("settings-cancel")
        rig.send("settings-open")
        self.assertTrue(rig.state()["settings"]["startAutomatically"])

    def test_save_is_refused_while_an_operation_runs(self):
        rig = BridgeRig()
        self.addCleanup(rig.close)
        rig.send("play")
        rig.send("settings-open")
        state = rig.save(startAutomatically=False)
        self.assertEqual(state["notice"]["text"], "Wait for the current operation to finish.")
        self.assertTrue(rig.file()["startAutomatically"])

    def test_settings_survive_a_restart(self):
        rig = BridgeRig()
        rig.send("settings-open")
        rig.save(startAutomatically=False, alertChime=False)
        rig.close()
        again = BridgeRig(config=False, base=rig.base)
        self.addCleanup(again.close)
        self.assertFalse(again.state()["settings"]["startAutomatically"])
        self.assertFalse(again.state()["settings"]["alertChime"])

    def test_cancel_changes_nothing(self):
        rig = BridgeRig(showWatch=False)
        self.addCleanup(rig.close)
        rig.send("settings-open")
        rig.send("settings-cancel")
        self.assertFalse(rig.file()["showWatch"])
        self.assertFalse(rig.state()["settings"]["showWatch"])

    def test_the_spot_is_kept_by_save_and_by_cancel(self):
        rig = BridgeRig()
        self.addCleanup(rig.close)
        rig.send("settings-open")
        rig.bridge._watch_moved(300, 200)
        rig.save(scale=1.25)
        self.assertEqual(rig.file()["watchSpot"], [300, 200])
        rig.send("settings-open")
        rig.bridge._watch_moved(500, 400)
        rig.send("settings-cancel")
        self.assertEqual(rig.file()["watchSpot"], [500, 400])

    def test_settings_covers_the_window_and_gear_state_is_in_the_snapshot(self):
        rig = BridgeRig()
        self.addCleanup(rig.close)
        self.assertFalse(rig.state()["settingsOpen"])
        self.assertTrue(rig.send("settings-open")["settingsOpen"])
        self.assertFalse(rig.send("settings-cancel")["settingsOpen"])

    def test_no_name_of_one_person_in_the_code(self):
        root = harness.ROOT
        for folder in ("launcher", "ui", "tools"):
            for path in (root / folder).rglob("*"):
                if path.suffix in (".py", ".js", ".html", ".css", ".json") and path.is_file():
                    self.assertNotIn("nobara", path.read_text(errors="ignore").lower(), str(path))
                    self.assertNotIn("/home/lonnie", path.read_text(errors="ignore").lower(), str(path))


if __name__ == "__main__":
    unittest.main()
