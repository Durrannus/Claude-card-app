import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from card_logger import updater


def make_zip(files: dict[str, str], root="Claude-card-app-main") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(f"{root}/{name}", text)
    return buf.getvalue()


NEW = {
    "card_logger/__init__.py": "",
    "card_logger/VERSION": "2026.10.01.1\n",
    "card_logger/gui.py": "new gui",
    "card_logger/brand_new.py": "added",
    "start_card_logger.pyw": "new start",
    "README.md": "new readme",
}


class UpdaterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = Path(self.tmp.name)
        (self.app / "card_logger").mkdir()
        for name, text in {"card_logger/__init__.py": "", "card_logger/VERSION": "2026.09.27.1",
                           "card_logger/gui.py": "old gui", "card_logger/limitless.py": "removed module",
                           "my_notes.txt": "not ours"}.items():
            (self.app / name).write_text(text)

    def tearDown(self):
        self.tmp.cleanup()

    def test_versions(self):
        self.assertTrue(updater.is_newer("2026.10.01.1", "2026.09.27.1"))
        self.assertTrue(updater.is_newer("2026.09.27.2", "2026.09.27.1"))
        self.assertFalse(updater.is_newer("2026.09.27.1", "2026.09.27.1"))
        self.assertFalse(updater.is_newer("2026.09.1", "2026.09.27.1"))
        self.assertEqual(updater.latest_version(lambda url: b"2026.10.01.1\n"), "2026.10.01.1")
        with self.assertRaises(updater.UpdateError):
            updater.latest_version(lambda url: b"<html>not found</html>")

    def test_install(self):
        version = updater.install(lambda url, timeout=0: make_zip(NEW), app_dir=self.app)
        self.assertEqual(version, "2026.10.01.1")
        read = lambda n: (self.app / n).read_text()
        self.assertEqual(read("card_logger/gui.py"), "new gui")
        self.assertEqual(read("card_logger/brand_new.py"), "added")
        self.assertEqual(read("start_card_logger.pyw"), "new start")
        self.assertFalse((self.app / "card_logger/limitless.py").exists())  # old module removed
        self.assertEqual(read("my_notes.txt"), "not ours")                   # other files untouched

    def test_bad_downloads_change_nothing(self):
        for data in (b"not a zip", make_zip({"something/else.txt": "x"})):
            with self.assertRaises(updater.UpdateError):
                updater.install(lambda url, timeout=0, d=data: d, app_dir=self.app)
            self.assertEqual((self.app / "card_logger/gui.py").read_text(), "old gui")

    def test_refuses_git_checkout(self):
        (self.app / ".git").mkdir()
        with self.assertRaisesRegex(updater.UpdateError, "git pull"):
            updater.install(lambda url, timeout=0: make_zip(NEW), app_dir=self.app)

    def test_current_version_file_exists(self):
        self.assertTrue(updater.parse_version(updater.current_version())[0] >= 2026)


if __name__ == "__main__":
    unittest.main()
