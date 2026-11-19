"""Pure-logic tests. No window opens and no recording starts.

Run from the repo folder:  python -m unittest discover -s tests
"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import easyrecord  # noqa: E402  (the module only opens a window under __main__)


class QualityTest(unittest.TestCase):
    def test_quality_to_crf(self):
        self.assertEqual(easyrecord.quality_to_crf(0), 40)
        self.assertEqual(easyrecord.quality_to_crf(50), 28)
        self.assertEqual(easyrecord.quality_to_crf(100), 16)
        self.assertEqual(easyrecord.quality_to_crf(-5), 40)
        self.assertEqual(easyrecord.quality_to_crf(500), 16)


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / ".easyrecord.json"
        # Never touch the real settings file in the home folder.
        self.patch = mock.patch.object(easyrecord, "SETTINGS_FILE", self.file)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_missing_file_gives_defaults(self):
        self.assertEqual(easyrecord.load_settings(), easyrecord.DEFAULTS)

    def test_broken_file_gives_defaults(self):
        self.file.write_text("{not json")
        self.assertEqual(easyrecord.load_settings(), easyrecord.DEFAULTS)

    def test_saved_values_merge_with_defaults(self):
        self.file.write_text(json.dumps({"quality": 55}))
        s = easyrecord.load_settings()
        self.assertEqual(s["quality"], 55)
        self.assertEqual(s["fps"], easyrecord.DEFAULTS["fps"])

    def test_save_then_load(self):
        easyrecord.save_settings(dict(easyrecord.DEFAULTS, fps=60))
        self.assertEqual(easyrecord.load_settings()["fps"], 60)

    def test_defaults_are_not_shared(self):
        easyrecord.load_settings()["quality"] = 1
        self.assertEqual(easyrecord.DEFAULTS["quality"], 80)


class FindFfmpegTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()  # the code resolves paths too
        self.app_dir, self.exe_dir = base / "app", base / "exe"
        self.app_dir.mkdir()
        self.exe_dir.mkdir()
        self.patches = [
            mock.patch.object(sys, "_MEIPASS", str(self.app_dir), create=True),
            mock.patch.object(sys, "executable", str(self.exe_dir / "python.exe")),
            mock.patch.object(easyrecord.shutil, "which", return_value="on-path-ffmpeg"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_next_to_app_wins(self):
        (self.app_dir / "ffmpeg.exe").write_bytes(b"")
        (self.exe_dir / "ffmpeg.exe").write_bytes(b"")
        self.assertEqual(easyrecord.find_ffmpeg(), str(self.app_dir / "ffmpeg.exe"))

    def test_exe_folder_second(self):
        (self.exe_dir / "ffmpeg.exe").write_bytes(b"")
        self.assertEqual(easyrecord.find_ffmpeg(), str(self.exe_dir / "ffmpeg.exe"))

    def test_path_last(self):
        self.assertEqual(easyrecord.find_ffmpeg(), "on-path-ffmpeg")


class ReleaseFilesTest(unittest.TestCase):
    def read(self, *parts):
        return ROOT.joinpath(*parts).read_text(encoding="utf-8")

    def test_ffmpeg_notice_has_no_fixed_version(self):
        text = self.read("installer", "FFMPEG-LICENSE.txt")
        self.assertNotRegex(text, r"(?i)version\s+\d+\.\d")
        self.assertIn("GPL", text)

    def test_installer_publisher(self):
        self.assertIn("AppPublisher=tbrains1998-web", self.read("installer", "EasyRecord.iss"))

    def test_build_uses_local_venv(self):
        # The build must not install or upgrade packages in the user's main Python.
        text = self.read("build.bat")
        self.assertIn(".venv", text)
        self.assertNotRegex(text, r"(?mi)^\s*python -m pip install")

    def test_ffmpeg_and_build_output_are_ignored(self):
        ignored = self.read(".gitignore").split()
        for name in ("ffmpeg.exe", "*.spec", "dist/", "build/", ".venv/"):
            self.assertIn(name, ignored)

    def test_build_checks_python_by_running_it(self):
        # 'where python' also finds the Microsoft Store stub on a PC with no Python.
        text = self.read("build.bat")
        self.assertNotIn("where python", text)
        self.assertIn('python -c "import sys"', text)

    def test_build_remakes_a_broken_venv(self):
        # A .venv made from a Python that was removed later must be made again.
        self.assertRegex(self.read("build.bat"),
                         r'(?i)\.venv\\Scripts\\python(?:\.exe)? -c "import sys".*\|\|.*rmdir /s /q \.venv')

    def test_build_paths_are_quoted(self):
        # A project path with '&' in it breaks an unquoted %~dp0 in cmd.
        for n, line in enumerate(self.read("build.bat").splitlines(), 1):
            for m in re.finditer(r"%~dp0", line):
                self.assertEqual(line[:m.start()].count('"') % 2, 1, f"build.bat:{n} unquoted %~dp0")

    def test_installer_ships_both_licenses(self):
        iss = self.read("installer", "EasyRecord.iss")
        self.assertRegex(iss, r'Source: "\.\.\\LICENSE"; DestDir: "\{app\}"; DestName: "LICENSE\.txt"')
        self.assertIn('Source: "GPL-3.0.txt"', iss)
        gpl = self.read("installer", "GPL-3.0.txt")
        self.assertIn("GNU GENERAL PUBLIC LICENSE", gpl)
        self.assertIn("Version 3, 29 June 2007", gpl)
        self.assertIn("GPL-3.0.txt", self.read("installer", "FFMPEG-LICENSE.txt"))

    def test_installed_readme(self):
        text = self.read("README.txt")
        self.assertIn("https://github.com/tbrains1998-web/easyrecord", text)
        # Installed users read how to use it first; building is only for the source download.
        self.assertLess(text.index("USE IT"), text.index("MAKE THE APP"))
        self.assertIn("%USERPROFILE%\\Videos\\EasyRecord", text)

    def test_readme_matches_setup(self):
        readme = self.read("README.md")
        self.assertIn("%USERPROFILE%\\Videos\\EasyRecord", readme)
        self.assertNotIn("your `Videos\\EasyRecord` folder", readme)
        self.assertIn("Compile", readme)  # ISCC.exe is not on PATH after installing Inno Setup
        self.assertIn("only when you run from source", readme)  # build.bat ignores a winget FFmpeg
        self.assertIn("delete the `.venv` folder", readme)

    def test_readme_and_license(self):
        self.assertIn("MIT License", self.read("LICENSE"))
        readme = self.read("README.md").lower()
        for word in ("ffmpeg", "winget install gyan.ffmpeg", "gpl", "mit", "python -m unittest"):
            self.assertIn(word, readme)

    def test_line_endings_fixed_by_gitattributes(self):
        # A clone (any core.autocrlf) or ZIP must give cmd a CRLF build.bat and byte-exact pictures.
        rules = self.read(".gitattributes").split()
        for rule in ("*.bat", "eol=crlf", "*.ico", "*.png", "binary"):
            self.assertIn(rule, rules)
        data = (ROOT / "build.bat").read_bytes()
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), "build.bat must be CRLF")


if __name__ == "__main__":
    unittest.main()
