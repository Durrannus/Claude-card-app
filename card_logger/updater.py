"""Check GitHub for a newer version of the app and install it in place.

The latest version lives on the repository's `main` branch. Its version
number is in card_logger/VERSION; if that's newer than the installed one,
the branch is downloaded as a ZIP and its files are copied over the app
folder. The collection, prices, decklists and settings are stored in the
.card_logger folder in the user's home folder, not in the app folder, so
updating never touches them.
"""

import io
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO = "Durrannus/Claude-card-app"
BRANCH = "main"
VERSION_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/card_logger/VERSION"
ZIP_URL = f"https://github.com/{REPO}/archive/refs/heads/{BRANCH}.zip"
HEADERS = {"User-Agent": "CardCollectionLogger-updater"}

PACKAGE_DIR = Path(__file__).resolve().parent
APP_DIR = PACKAGE_DIR.parent


class UpdateError(Exception):
    """Update failed; the message is suitable for showing to the user."""


def current_version() -> str:
    try:
        return (PACKAGE_DIR / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "0"


def parse_version(text: str) -> tuple[int, ...]:
    parts = []
    for piece in text.strip().split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer(candidate: str, installed: str) -> bool:
    return parse_version(candidate) > parse_version(installed)


def _download(url: str, timeout: int = 30) -> bytes:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise UpdateError(f"GitHub returned an error ({e.code}). Try again later.") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpdateError("Could not reach GitHub. Check your internet connection.") from e


def latest_version(download=_download) -> str:
    text = download(VERSION_URL).decode("utf-8", "replace").strip()
    if not text or not parse_version(text)[0]:
        raise UpdateError("GitHub sent an unexpected version number.")
    return text


def install(download=_download, app_dir: Path = APP_DIR, report=None) -> str:
    """Download the latest version and copy it over the app folder.

    Returns the newly installed version number."""
    if (app_dir / ".git").exists():
        raise UpdateError("This copy of the app is managed with git. Update it with \"git pull\" instead.")
    if report:
        report("Downloading the latest version…")
    data = download(ZIP_URL, timeout=120)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise UpdateError("The download was damaged. Try again.") from e

    with tempfile.TemporaryDirectory() as tmp:
        archive.extractall(tmp)
        roots = [p for p in Path(tmp).iterdir() if p.is_dir()]
        if len(roots) != 1 or not (roots[0] / "card_logger" / "__init__.py").exists():
            raise UpdateError("The download didn't look like this app. Nothing was changed.")
        new = roots[0]
        new_version = (new / "card_logger" / "VERSION").read_text(encoding="utf-8").strip() \
            if (new / "card_logger" / "VERSION").exists() else "?"
        if report:
            report(f"Installing version {new_version}…")

        # Copy every file of the new version over the old one.
        for source in new.rglob("*"):
            if source.is_file():
                target = app_dir / source.relative_to(new)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

        # Remove program files the new version no longer has, so old code
        # can't linger. Only .py files inside the package are touched.
        new_modules = {p.name for p in (new / "card_logger").glob("*.py")}
        for old in (app_dir / "card_logger").glob("*.py"):
            if old.name not in new_modules:
                old.unlink()
        shutil.rmtree(app_dir / "card_logger" / "__pycache__", ignore_errors=True)
    return new_version


def restart_command() -> list[str]:
    """How to start the app again the same way it was started."""
    script = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else None
    if script and script.name == "__main__.py":
        return [sys.executable, "-m", "card_logger", *sys.argv[1:]]
    if script and script.exists():
        return [sys.executable, str(script), *sys.argv[1:]]
    return [sys.executable, "-m", "card_logger"]
