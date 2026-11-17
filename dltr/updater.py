"""Check GitHub for a newer dl_transcribe (repo: dl_audio_transcriber) and install it.

The app asks GitHub for the latest *release* of Open-Grapes/dl_audio_transcriber, compares its tag (e.g. v1.3.0) with
this version, downloads dl_transcribe-windows.zip from that release and replaces the app: a small helper script
waits for the app to close, copies the new files over the old ones and starts the new version. Downloaded models
and settings live in %LOCALAPPDATA% and are not touched.

Private repository: GitHub hides private releases from anonymous requests, so ordinary users only get updates
once the repo is public. On a computer that is logged in to GitHub with access to the repo (the `gh` command-line
tool, or a GH_TOKEN / GITHUB_TOKEN variable) the check retries with that login, so the owner can test updates
while the repo is still private. No login is built into the app.

Testing: set DLTR_UPDATE_API to a URL that returns release JSON (same shape as GitHub's) to point the app at a
fake release instead of GitHub.
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from .common import APP_NAME, APP_VERSION, IS_MAC, IS_WIN, RELEASE_ASSET, SUP

REPO = "Open-Grapes/dl_audio_transcriber"
API = os.environ.get("DLTR_UPDATE_API") or f"https://api.github.com/repos/{REPO}/releases/latest"
TESTING = bool(os.environ.get("DLTR_UPDATE_API"))
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def asset_name():
    """Name of the release file for this computer."""
    if IS_MAC:
        return f"{APP_NAME}-mac-{'apple-silicon' if platform.machine() == 'arm64' else 'intel'}.dmg"
    return RELEASE_ASSET


class UpdateError(Exception):
    pass


def parse_version(text):
    """'v1.10.2' -> (1, 10, 2); anything unparsable sorts lowest."""
    parts = []
    for p in str(text).strip().lstrip("vV").split("."):
        digits = "".join(c for c in p if c.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts) or (0,)


def is_newer(remote, local=APP_VERSION):
    return parse_version(remote) > parse_version(local)


def _token():
    """This computer's own GitHub login, if any (used only after an anonymous request was refused)."""
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(var):
            return os.environ[var].strip()
    gh = shutil.which("gh")
    if not gh and IS_WIN:
        c = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "GitHub CLI" / "gh.exe"
        gh = str(c) if c.exists() else None
    if not gh and IS_MAC:
        gh = next((c for c in ("/opt/homebrew/bin/gh", "/usr/local/bin/gh") if os.path.exists(c)), None)
    if not gh:
        return None
    try:
        out = subprocess.run([gh, "auth", "token", "--hostname", "github.com"], capture_output=True, text=True,
                             timeout=8, creationflags=NO_WINDOW)
        return out.stdout.strip() or None
    except Exception:
        return None


def _get(url, token=None, accept="application/vnd.github+json", timeout=20):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": f"{APP_NAME}/{APP_VERSION}",
                                               "X-GitHub-Api-Version": "2022-11-28"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context())


def _ssl_context():
    """The Mac app has no system certificate store hooked up to Python, so bring certifi's."""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def latest():
    """Latest release: dict(version, notes, page, published, asset{name,size,url,api_url} or None, token, private)."""
    token = None
    try:
        with _get(API) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code not in (401, 403, 404) or TESTING:
            raise UpdateError(f"GitHub answered {e.code}. Try again later.") from None
        token = _token()
        if not token:
            raise UpdateError("No published version was found. The dl_transcribe project on GitHub is private "
                              "(or has no releases yet), so updates aren't available yet.") from None
        try:
            with _get(API, token) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e2:
            if e2.code == 404:
                try:
                    with _get(f"https://api.github.com/repos/{REPO}", token):
                        pass
                except urllib.error.HTTPError:
                    raise UpdateError(f"The project {REPO} isn't on GitHub yet (or this computer's GitHub login "
                                      "can't see it).") from None
                raise UpdateError("Your GitHub login can see the project, but no release has been published "
                                  "yet.") from None
            raise UpdateError(f"GitHub refused the request ({e2.code}), even with this computer's GitHub "
                              "login.") from None
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError(f"Couldn't reach GitHub. Check your internet connection. ({e})") from None

    want = asset_name()
    asset = next((a for a in data.get("assets", []) if a.get("name") == want), None)
    return {
        "version": str(data.get("tag_name", "")).lstrip("vV"),
        "notes": (data.get("body") or "").strip(),
        "page": data.get("html_url") or RELEASES_PAGE,
        "published": (data.get("published_at") or "")[:10],
        "asset": {"name": want, "size": asset.get("size", 0), "url": asset.get("browser_download_url"),
                  "api_url": asset.get("url")} if asset else None,
        "token": token,
        "private": bool(token),
    }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def download(info, dest_dir, progress=None, cancel=None):
    """Download the release zip into dest_dir. progress(done_bytes, total_bytes)."""
    asset = info.get("asset")
    if not asset:
        raise UpdateError(f"The latest version has no {asset_name()} file to download.")
    dest = Path(dest_dir) / asset["name"]
    token = info.get("token")
    if token:
        # Private repo: ask the API for the file; it answers with a short-lived link that must be fetched
        # without the login header.
        opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=_ssl_context()))
        req = urllib.request.Request(asset["api_url"], headers={
            "Accept": "application/octet-stream", "Authorization": f"Bearer {token}",
            "User-Agent": f"{APP_NAME}/{APP_VERSION}"})
        try:
            r = opener.open(req, timeout=30)
            url, first = None, r
        except urllib.error.HTTPError as e:
            if e.code not in (301, 302, 303, 307, 308):
                raise UpdateError(f"Download refused ({e.code}).") from None
            url, first = e.headers["Location"], None
        resp = first or _get(url, accept="application/octet-stream", timeout=30)
    else:
        resp = _get(asset["url"], accept="application/octet-stream", timeout=30)
    total = int(resp.headers.get("Content-Length") or asset.get("size") or 0)
    done = 0
    with resp, open(dest, "wb") as fh:
        while True:
            if cancel and cancel.is_set():
                raise UpdateError("Cancelled")
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if total and done != total:
        raise UpdateError("The download was cut short. Try again.")
    return dest


# ---------------------------------------------------------------- installing

def app_location():
    """The folder holding dl_transcribe.exe (Windows) or the .app bundle (Mac); None when running from source."""
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    if IS_MAC:
        return next((p for p in exe.parents if p.suffix == ".app"), None)
    return exe.parent


def can_install():
    """(True, None) or (False, reason) for replacing the running app in place."""
    loc = app_location()
    if loc is None:
        return False, "This copy runs from the source code; update it with git pull instead."
    if not os.access(loc.parent if IS_MAC else loc, os.W_OK):
        return False, f"dl_transcribe can't write to {loc}. Download the new version from GitHub instead."
    return True, None


def prepare_install(package, pid, launch=True, loc=None):
    """Unpack the download and write the helper that replaces the app (`loc`, default: this app) once process
    `pid` has exited. Returns (command to start detached before quitting, work folder)."""
    loc = Path(loc) if loc else app_location()
    if loc is None:
        raise UpdateError("This copy runs from the source code; update it with git pull instead.")
    work = Path(tempfile.mkdtemp(prefix="dltr_update_", dir=_work_root(clean=False)))
    if IS_MAC:
        return _prepare_mac(Path(package), pid, launch, loc, work)
    try:
        with zipfile.ZipFile(package) as z:
            z.extractall(work / "new")
    except zipfile.BadZipFile:
        raise UpdateError("The downloaded file is damaged. Try again.") from None
    new = work / "new" / APP_NAME
    if not (new / f"{APP_NAME}.exe").exists():
        raise UpdateError(f"The downloaded file doesn't contain {APP_NAME}.exe.")
    Path(package).unlink(missing_ok=True)       # the unpacked copy is all we need now
    script = work / "update.cmd"
    launch_line = f'start "" "{loc}\\{APP_NAME}.exe"' if launch else "rem (no relaunch)"
    script.write_text(f"""@echo off
rem dl_transcribe updater: wait for the app to close, copy the new files over it, start it again.
:wait
tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul && (ping -n 2 127.0.0.1 >nul & goto wait)
ping -n 3 127.0.0.1 >nul
robocopy "{new}\\_internal" "{loc}\\_internal" /MIR /R:10 /W:1 /NFL /NDL /NJH /NJS /NP >nul
robocopy "{new}" "{loc}" /E /XD "{new}\\_internal" /R:10 /W:1 /NFL /NDL /NJH /NJS /NP >nul
echo done> "{work}\\finished.txt"
rmdir /s /q "{work}\\new"
{launch_line}
""", encoding="ascii")
    return ["cmd", "/c", str(script)], work


def _prepare_mac(package, pid, launch, loc, work):
    script = work / "update.sh"
    launch_line = f'open "{loc}"' if launch else ": # (no relaunch)"
    script.write_text(f"""#!/bin/bash
# dl_transcribe updater: wait for the app to quit, copy the new app from the .dmg over it, open it again.
while kill -0 {pid} 2>/dev/null; do sleep 0.5; done
sleep 1
MNT="$(mktemp -d)"
hdiutil attach -nobrowse -readonly -mountpoint "$MNT" "{package}" >/dev/null || exit 1
rm -rf "{loc}.new"
if ditto "$MNT/{APP_NAME}.app" "{loc}.new"; then
  rm -rf "{loc}" && mv "{loc}.new" "{loc}"
fi
hdiutil detach "$MNT" -quiet
rm -f "{package}"
echo done > "{work}/finished.txt"
{launch_line}
""")
    script.chmod(0o755)
    return ["/bin/bash", str(script)], work


def _work_root(clean=True):
    """Folder for update downloads. clean=True first removes leftovers of earlier or abandoned updates; it must
    be False while a download is being unpacked (that download lives in this folder too)."""
    root = SUP / "updates"
    root.mkdir(parents=True, exist_ok=True)
    if not clean:
        return root
    for old in root.iterdir():                      # leftovers from earlier or abandoned updates
        if old.name.startswith("dltr_update_") and (old / "finished.txt").exists() or \
                old.name.startswith(("dltr_dl_", "tmp")):
            shutil.rmtree(old, ignore_errors=True)
    return root


def start_helper(cmd):
    """Start the helper so it outlives this process."""
    if IS_WIN:
        # A hidden console (not DETACHED_PROCESS): tasklist and robocopy need one.
        flags = NO_WINDOW | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(cmd, creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(cmd, start_new_session=True, close_fds=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
