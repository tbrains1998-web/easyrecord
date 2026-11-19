"""Scan the files git would commit for things that should never be public.

Only general patterns are used here (no personal words), so the test works on any PC.
Files .gitignore keeps out (your recordings, settings, build output) are not scanned.
"""
import re
import subprocess
import unittest
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEXT_EXT = {".py", ".md", ".txt", ".bat", ".iss", ".json", ".cfg", ".toml", ".ps1", ""}

ALLOWED_IPS = ("127.0.0.1", "0.0.0.0")
TEST_NETS = ("192.0.2.", "198.51.100.", "203.0.113.")

# Some words are joined at run time so this file does not flag itself.
PATTERNS = {
    "absolute Windows path": re.compile(r"(?<![\w%])[A-Za-z]:[\\/]"),
    "home folder path": re.compile(r"(?i)[\\/]" + "us" + r"ers[\\/][^\\/\s]+"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z][\w.-]*"),
    "API key or token": re.compile(
        r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}"
        r"|AIza[0-9A-Za-z_-]{30,}|xox[abp]-[A-Za-z0-9-]{10,})"
        r"|(?i:(?:api[_-]?key|token|secret|password)\s*[:=]\s*['\"][^'\"\s]{12,}['\"])"),
    "IPv4 address": re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])"),
    "locale pin": re.compile(r"(?<![\w-])[a-z]{2}[-_][A-Z]{2}(?![\w-])"),
    "time zone name": re.compile(
        r"\b(?:Europe|America|Asia|Africa|Australia|Pacific|Atlantic|Indian)/[A-Z][a-z]+"),
    "country code in a URL": re.compile(
        r"(?i)https?://[\w.-]+\.(?!io\b|ai\b)[a-z]{2}(?![\w.-])"
        r"|https?://[^\s\"'<>]*/[a-z]{2}(?:[-_][a-z]{2})?/"),
    "job note": re.compile(
        r"\b(?:" + "PL" + r"AN\d?\.md|" + "BU" + r"GS\.md|" + "re" + r"con\b|"
        + "fix" + r" round|[Pp]art [A-H]\b)"
        r"|(?<![<\w-])B\d{1,3}(?![\w-])"
        r"|\buser,? 20\d\d-\d\d-\d\d"),
}


def gitignore_rules():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    return [".git/"] + [l.strip() for l in lines if l.strip() and not l.startswith("#")]


def is_ignored(rel, rules):
    """Simple copy of git's rules for the patterns our .gitignore uses (no '!' rules)."""
    rel = Path(rel)
    for rule in rules:
        if rule.endswith("/"):
            if any(fnmatch(part, rule[:-1]) for part in rel.parts[:-1]):
                return True
        elif "/" in rule:
            if fnmatch(rel.as_posix(), rule.lstrip("/")):
                return True
        elif any(fnmatch(part, rule) for part in rel.parts):
            return True
    return False


def repo_files():
    """Files git would add: tracked plus untracked-but-not-ignored."""
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True, check=True).stdout.decode("utf-8")
        return [ROOT / n for n in out.split("\0") if n and (ROOT / n).is_file()]
    except (OSError, subprocess.CalledProcessError):  # no git, or not a git clone yet
        rules = gitignore_rules()
        return [p for p in ROOT.rglob("*")
                if p.is_file() and not is_ignored(p.relative_to(ROOT), rules)]


def allowed(kind, hit):
    if kind == "email":
        return hit.split("@")[1].startswith(("users.noreply.github.com", "example."))
    if kind == "IPv4 address":
        return hit in ALLOWED_IPS or hit.startswith(TEST_NETS)
    return False


class PrivacyTest(unittest.TestCase):
    def test_no_private_patterns(self):
        problems = []
        for path in repo_files():
            if path.suffix.lower() not in TEXT_EXT and not path.name.startswith("."):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for kind, rx in PATTERNS.items():
                for m in rx.finditer(text):
                    if not allowed(kind, m.group(0)):
                        line = text.count("\n", 0, m.start()) + 1
                        problems.append(f"{path.relative_to(ROOT)}:{line}: {kind}: {m.group(0)!r}")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_no_backup_log_or_recording_files(self):
        bad = [str(p.relative_to(ROOT)) for p in repo_files()
               if re.search(r"\.(?:bak\w*|log|mp4|mkv|mov|avi|webm|jpe?g|bmp)$", p.name, re.I)
               or p.name.startswith("EasyRecord ")
               or (p.suffix.lower() == ".png" and p.name != "icon.png")]
        self.assertEqual(bad, [])

    def test_recordings_and_join_list_are_ignored(self):
        rules = gitignore_rules()
        for name in ("EasyRecord 2026-01-01 10-00-00.mp4", "EasyRecord 2026-01-01 10-00-00.txt",
                     "EasyRecord 2026-01-01 10-00-00.png", "sub/EasyRecord 2026-01-01 10-00-00.jpg",
                     "clip.mp4", ".easyrecord.json", "dist/EasyRecord.exe"):
            self.assertTrue(is_ignored(name, rules), name)
        for name in ("icon.png", "easyrecord.py", "installer/EasyRecord.iss", "LICENSE",
                     "installer/GPL-3.0.txt"):
            self.assertFalse(is_ignored(name, rules), name)

    def test_region_patterns_catch_examples(self):
        # Examples are built at run time so this file does not flag itself.
        # They use the unassigned code "zz" so they point at no real country.
        for kind, sample in (("locale pin", "lang = '" + "zz" + "-ZZ'"),
                             ("time zone name", "tz = 'Atlan" + "tic/Example'"),
                             ("country code in a URL", "https://shop.example." + "zz/x"),
                             ("country code in a URL", "https://example.com/" + "zz" + "/help")):
            self.assertRegex(sample, PATTERNS[kind], kind)


if __name__ == "__main__":
    unittest.main()
