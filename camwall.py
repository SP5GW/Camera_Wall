"""CamWall launcher.

Reads cameras.yaml + .env, writes go2rtc.yaml and www/cameras.json, then
starts go2rtc, which converts the camera streams to WebRTC and serves the
web page on http://localhost:1984.

Passwords never reach go2rtc.yaml or the browser: the generated config only
contains ${CAMWALL_...} placeholders, and the real (URL-encoded) values are
handed to go2rtc as environment variables when it starts.

Runs on Windows 10/11, Raspberry Pi OS and other Linux, and macOS.

Usage:
    python camwall.py              validate, generate files, start go2rtc
    python camwall.py --check      validate and generate files only
    python camwall.py --download   download go2rtc for this computer
    python camwall.py --help       show help (features, running, installation)
"""

import io
import ipaddress
import json
import os
import platform
import re
import shutil
import socket
import stat
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import quote, urlsplit

try:
    import yaml
except ImportError:
    if os.name == "nt":
        print("ERROR: PyYAML is missing. Install it with:  python -m pip install pyyaml")
    else:
        print("ERROR: PyYAML is missing. Install it with:  sudo apt install python3-yaml")
    sys.exit(1)

ROOT = Path(__file__).resolve().parent
CAMERAS_FILE = ROOT / "cameras.yaml"
ENV_FILE = ROOT / ".env"
GO2RTC_CONFIG = ROOT / "go2rtc.yaml"
WWW = ROOT / "www"
PORT = 1984

HELP = """\
CamWall 1.0 - pomoc
===================
Październik 2026, Andrzej Mazur SP5GW

CamWall pokazuje obraz ze wszystkich kamer IP z pliku cameras.yaml na jednej
stronie WWW. Działa w całości lokalnie, bez chmury i bez konta.

FUNKCJE
  - Wszystkie kamery na jednym ekranie; układ dopasowuje się do okna.
  - Filtry grup (np. Outside / Inside) według pola "group" w cameras.yaml.
  - Status kamery: Live / Connecting / Offline z podaną przyczyną.
  - Automatyczne ponowne łączenie i wykrywanie zatrzymanego obrazu.
  - Podwójne kliknięcie kamery powiększa ją; strzałki <- -> zmieniają kamerę;
    podwójne kliknięcie, Esc lub X zamyka widok; F to pełny ekran;
    ? otwiera pomoc na stronie.
  - Hasła są tylko w pliku .env i nie trafiają do przeglądarki.

URUCHOMIENIE
  Windows:       dwuklik na start.bat
  Raspberry Pi:  bash start.sh   (albo autostart, patrz niżej)
  Strona:        http://localhost:1984
                 z innego urządzenia: http://<IP komputera>:1984
  Zatrzymanie:   Ctrl+C w oknie CamWall
  Po zmianie cameras.yaml lub .env uruchom CamWall ponownie.
  Teksty strony i tłumaczenia: folder www/lang (opis w www/lang/README.txt).

  Polecenia:
    python camwall.py              sprawdza konfigurację i uruchamia CamWall
    python camwall.py --check      tylko sprawdza konfigurację i tłumaczenia
    python camwall.py --download   pobiera go2rtc dla tego komputera
    python camwall.py --help       ta pomoc
  (na Raspberry Pi użyj python3 zamiast python)

DOSTĘP SPOZA DOMU (OPCJONALNIE)
  Zainstaluj Tailscale (tailscale.com) na komputerze z CamWall i na telefonie,
  zaloguj się tym samym kontem. CamWall wykryje Tailscale przy starcie i pokaże
  adres do otwarcia poza domem. Bez Tailscale działa tylko w sieci domowej.
  Ustawienie w cameras.yaml:   settings:
                                 tailscale: auto   (auto / on / off)

INSTALACJA - WINDOWS 10/11
  1. Zainstaluj Python 3 z python.org; zaznacz "Add python.exe to PATH".
  2. Skopiuj cały folder cam_wall, razem z plikiem .env.
  3. Uruchom start.bat. Za pierwszym razem doinstaluje PyYAML i zaproponuje
     pobranie go2rtc; odpowiedz Y.
  4. Zezwól go2rtc na dostęp przez Zaporę Windows (sieci prywatne).

INSTALACJA - RASPBERRY PI
  1. Skopiuj folder cam_wall na Pi (pendrive albo:
     scp -r cam_wall użytkownik@IP-Pi:~/ ).
  2. cd ~/cam_wall
     bash start.sh
     Doinstaluje python3-yaml i pobierze go2rtc dla Pi; odpowiedz Y.
  3. Autostart po włączeniu Pi:   bash install-service.sh
     logi:                        journalctl -u camwall -f
     restart po zmianach:         sudo systemctl restart camwall
     usunięcie autostartu:        bash install-service.sh --remove
  4. Ściana kamer na monitorze Pi:
     chromium-browser --kiosk http://localhost:1984
"""

VAR_RE = re.compile(r"\$\{(\w+)\}")
ID_RE = re.compile(r"^[A-Za-z0-9_]+$")


class ConfigError(Exception):
    pass


def load_env(path):
    """Parse a simple KEY=value .env file; real environment variables win."""
    values = {}
    if path.exists():
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise ConfigError(f".env line {n}: expected KEY=value")
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key.strip()] = value
    values.update({k: v for k, v in os.environ.items() if k not in values})
    return values


def text(value):
    return "" if value is None else str(value).strip()


class UrlBuilder:
    """Turns camera URLs into go2rtc sources with env-var placeholders."""

    def __init__(self, env):
        self.env = env
        self.go2rtc_env = {}  # CAMWALL_* -> URL-encoded secret

    def _placeholder(self, name, value):
        key = f"CAMWALL_{name.upper()}"
        self.go2rtc_env[key] = quote(value, safe="")
        return "${" + key + "}"

    def _lookup(self, var, where):
        if var not in self.env:
            raise ConfigError(f"{where}: ${{{var}}} is not defined in .env")
        return self.env[var]

    def build(self, cam, field):
        where = f"camera '{cam['id']}', {field}"
        url = text(cam.get(field))

        # ${VAR} written directly inside the URL (e.g. rtsp://admin:${CAM1}@...)
        def repl(m):
            return self._placeholder(m.group(1), self._lookup(m.group(1), where))

        url = VAR_RE.sub(repl, url)

        parts = urlsplit(url)
        if parts.scheme not in ("rtsp", "rtsps", "http", "https"):
            raise ConfigError(f"{where}: unsupported URL '{parts.scheme}://...'")
        if not parts.hostname:
            raise ConfigError(f"{where}: URL has no host")

        # No credentials in the URL: add them from username/password fields.
        username = text(cam.get("username"))
        if "@" not in parts.netloc and username:
            password = text(cam.get("password"))
            m = VAR_RE.fullmatch(password)
            if m:
                secret = self._placeholder(m.group(1), self._lookup(m.group(1), f"camera '{cam['id']}', password"))
            elif password:
                secret = self._placeholder(f"{cam['id']}_password", password)
            else:
                secret = ""
            auth = quote(username, safe="") + (":" + secret if secret else "")
            url = f"{parts.scheme}://{auth}@{parts.netloc}{url[len(parts.scheme) + 3 + len(parts.netloc):]}"
        return url


def load_cameras(env):
    try:
        data = yaml.safe_load(CAMERAS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"{CAMERAS_FILE.name} not found")
    except yaml.YAMLError as e:
        raise ConfigError(f"{CAMERAS_FILE.name} is not valid YAML:\n{e}")

    if not isinstance(data, dict) or not isinstance(data.get("cameras"), list):
        raise ConfigError(f"{CAMERAS_FILE.name} must contain a 'cameras:' list")

    builder = UrlBuilder(env)
    cameras, seen = [], set()
    for i, cam in enumerate(data["cameras"], 1):
        if not isinstance(cam, dict):
            raise ConfigError(f"camera #{i}: expected 'key: value' fields")
        if cam.get("enabled", True) is False:
            continue

        cam_id = text(cam.get("id"))
        if not cam_id:
            raise ConfigError(f"camera #{i}: missing 'id'")
        if not ID_RE.match(cam_id):
            raise ConfigError(f"camera '{cam_id}': id may contain only letters, digits and _")
        if cam_id in seen:
            raise ConfigError(f"camera '{cam_id}': id is used twice")
        seen.add(cam_id)
        cam["id"] = cam_id

        url_field = "rtsp_url" if cam.get("rtsp_url") else "url"
        if not text(cam.get(url_field)):
            raise ConfigError(f"camera '{cam_id}': missing 'rtsp_url'")

        main = builder.build(cam, url_field)
        # rtsp_substream_url matches rtsp_url; substream_url is the older name, still accepted.
        sub_field = "rtsp_substream_url" if text(cam.get("rtsp_substream_url")) else "substream_url"
        sub = builder.build(cam, sub_field) if text(cam.get(sub_field)) else None
        cameras.append({
            "id": cam_id,
            "name": text(cam.get("name")) or cam_id,
            "group": text(cam.get("group")),
            "model": text(cam.get("model")),
            "main": main,
            "sub": sub,
        })

    if not cameras:
        raise ConfigError("no enabled cameras in cameras.yaml")
    return cameras, builder.go2rtc_env, load_settings(data)


def load_settings(data):
    """Optional 'settings:' section of cameras.yaml."""
    settings = data.get("settings") or {}
    if not isinstance(settings, dict):
        raise ConfigError("'settings:' must contain 'key: value' fields")
    raw = settings.get("tailscale", "auto")
    # YAML reads unquoted on/off as true/false.
    mode = {True: "on", False: "off"}.get(raw, raw) if isinstance(raw, bool) else str(raw).strip().lower()
    if mode not in ("auto", "on", "off"):
        raise ConfigError(f"settings: 'tailscale' must be auto, on or off (found '{raw}')")
    return {"tailscale": mode}


# ---------------------------------------------------------------------------
# Tailscale (optional remote access). The page works through Tailscale as is;
# only the WebRTC video needs go2rtc to also offer this computer's Tailscale
# address, because by default it offers the home-network address only.
# ---------------------------------------------------------------------------

TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")
WEBRTC_PORT = 8555


def is_tailscale_ip(ip):
    try:
        return ipaddress.ip_address(ip) in TAILSCALE_NET
    except ValueError:
        return False


def tailscale_cli():
    exe = shutil.which("tailscale")
    if not exe and os.name == "nt":  # the Windows installer does not always add it to PATH
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            candidate = Path(base or "") / "Tailscale" / "tailscale.exe"
            if base and candidate.is_file():
                return str(candidate)
    return exe


def local_ipv4_addresses():
    try:
        return {info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    except OSError:
        return set()


def lan_ipv4():
    """The address this computer uses on its home network (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def detect_tailscale():
    """Return {"ip", "name"} if Tailscale is connected on this computer, else None."""
    exe = tailscale_cli()
    if exe:
        try:
            out = subprocess.run([exe, "status", "--json"], capture_output=True, text=True,
                                 encoding="utf-8", timeout=5)
            status = json.loads(out.stdout)
        except (OSError, subprocess.SubprocessError, ValueError):
            status = None
        if isinstance(status, dict):
            if status.get("BackendState") != "Running":  # installed but stopped / logged out
                return None
            me = status.get("Self") or {}
            ips = [ip for ip in me.get("TailscaleIPs") or [] if is_tailscale_ip(ip)]
            if not ips:
                return None
            name = (me.get("DNSName") or "").rstrip(".") or me.get("HostName") or ips[0]
            return {"ip": ips[0], "name": name}
    # No command-line tool (or it failed): look for a Tailscale address on this computer.
    for ip in sorted(local_ipv4_addresses()):
        if is_tailscale_ip(ip):
            return {"ip": ip, "name": ip}
    return None


def webrtc_candidates(settings):
    """Addresses go2rtc offers browsers for video; empty = go2rtc defaults (home network)."""
    mode = settings["tailscale"]
    if mode == "off":
        print("OK: Tailscale: off (settings in cameras.yaml) - home network only")
        return []
    ts = detect_tailscale()
    if not ts:
        if mode == "on":
            print("WARNING: tailscale: on, but Tailscale is not running on this computer - home network only")
        else:
            print("OK: Tailscale not detected - home network only")
        return []
    print(f"OK: Tailscale: http://{ts['name']}:{PORT}  ({ts['ip']})")
    # go2rtc may offer only the listed addresses, so list the home address too.
    ips = [ip for ip in (lan_ipv4(), ts["ip"]) if ip]
    return [f"{ip}:{WEBRTC_PORT}" for ip in dict.fromkeys(ips)]


def write_go2rtc_config(cameras, candidates=()):
    lines = [
        "# Generated by camwall.py from cameras.yaml - do not edit, changes are overwritten.",
        "# Contains no passwords: ${CAMWALL_...} values are passed in at startup.",
        "api:",
        f'  listen: ":{PORT}"',
        f"  static_dir: {json.dumps(str(WWW))}",
        "rtsp:",
        '  listen: ""   # no RTSP re-streaming: cameras are reachable only via the page',
    ]
    if candidates:
        lines += ["webrtc:", "  candidates:   # home network + Tailscale"]
        lines += [f"    - {c}" for c in candidates]
    lines.append("streams:")
    for cam in cameras:
        lines.append(f"  {cam['id']}: {json.dumps(cam['main'])}")
        if cam["sub"]:
            lines.append(f"  {cam['id']}_sub: {json.dumps(cam['sub'])}")
    GO2RTC_CONFIG.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_page_config(cameras):
    page = [{
        "id": c["id"],
        "name": c["name"],
        "group": c["group"],
        "model": c["model"],
        "stream": c["id"],
        "gridStream": f"{c['id']}_sub" if c["sub"] else c["id"],
    } for c in cameras]
    WWW.mkdir(exist_ok=True)
    (WWW / "cameras.json").write_text(json.dumps(page, ensure_ascii=False, indent=2), encoding="utf-8")


GO2RTC_RELEASES = "https://github.com/AlexxIT/go2rtc/releases/latest/download/"

# (system, machine) -> go2rtc release file. platform.machine() values vary by OS.
GO2RTC_ASSETS = {
    ("Windows", "amd64"): "go2rtc_win64.zip",
    ("Windows", "x86"): "go2rtc_win32.zip",
    ("Windows", "arm64"): "go2rtc_win_arm64.zip",
    ("Linux", "x86_64"): "go2rtc_linux_amd64",
    ("Linux", "i686"): "go2rtc_linux_i386",
    ("Linux", "aarch64"): "go2rtc_linux_arm64",   # Raspberry Pi 3/4/5, 64-bit OS
    ("Linux", "armv7l"): "go2rtc_linux_arm",      # Raspberry Pi 2/3/4, 32-bit OS
    ("Linux", "armv6l"): "go2rtc_linux_armv6",    # Raspberry Pi Zero / 1
    ("Darwin", "arm64"): "go2rtc_mac_arm64.zip",
    ("Darwin", "x86_64"): "go2rtc_mac_amd64.zip",
}


def go2rtc_asset():
    return GO2RTC_ASSETS.get((platform.system(), platform.machine().lower()))


def local_go2rtc_name():
    return "go2rtc.exe" if os.name == "nt" else "go2rtc"


LANG_DIR = WWW / "lang"
BASE_LANG = "en"
PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def build_language_index():
    """Check www/lang/<code>/ and write lang/languages.json for the page.

    Problems are only warnings: the page shows English for anything a
    translation is missing, and skips a language whose strings.json is broken.
    """
    if not LANG_DIR.is_dir():
        print(f"WARNING: {LANG_DIR.relative_to(ROOT)} not found - the page will have no texts")
        return []

    packs = {}
    for folder in sorted(p for p in LANG_DIR.iterdir() if p.is_dir()):
        path = folder / "strings.json"
        rel = path.relative_to(ROOT).as_posix()
        if not path.is_file():
            print(f"WARNING: {rel} not found - language '{folder.name}' skipped")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))  # -sig: Notepad may add a BOM
        except json.JSONDecodeError as e:
            print(f"WARNING: {rel} line {e.lineno}, column {e.colno}: {e.msg} - language '{folder.name}' skipped")
            continue
        except UnicodeDecodeError:
            print(f"WARNING: {rel} is not saved as UTF-8 - language '{folder.name}' skipped")
            continue
        if not isinstance(data, dict):
            print(f"WARNING: {rel} must be a {{ \"key\": \"text\" }} object - language '{folder.name}' skipped")
            continue
        packs[folder.name] = data

    base = packs.get(BASE_LANG)
    if base is None:
        print(f"WARNING: www/lang/{BASE_LANG} is missing - it is the fallback for missing texts")
        base = {}

    for code, data in packs.items():
        if code != BASE_LANG:
            missing = [k for k in base if not k.startswith("_") and k not in data]
            if missing:
                shown = ", ".join(missing[:6]) + (", ..." if len(missing) > 6 else "")
                print(f"WARNING: language '{code}': {len(missing)} text(s) missing, English used: {shown}")
            for key, text in data.items():
                if isinstance(text, str) and isinstance(base.get(key), str):
                    want = set(PLACEHOLDER_RE.findall(base[key]))
                    if set(PLACEHOLDER_RE.findall(text)) != want:
                        names = ", ".join("{" + p + "}" for p in sorted(want)) or "none"
                        print(f"WARNING: language '{code}', \"{key}\": placeholders should be {names}")
        for name in ("help.html", "add-camera.html"):
            if not (LANG_DIR / code / name).is_file():
                print(f"WARNING: www/lang/{code}/{name} not found - English version shown")

    index = []
    for code, data in packs.items():
        meta = data.get("_meta") if isinstance(data.get("_meta"), dict) else {}
        order = meta.get("order")
        index.append({
            "code": code,
            "label": str(meta.get("label") or code.upper()),
            "name": str(meta.get("name") or code),
            "locale": str(meta.get("locale") or code),
            "_order": order if isinstance(order, (int, float)) else 100,
        })
    index.sort(key=lambda lang: (lang["_order"], lang["code"]))
    for lang in index:
        del lang["_order"]
    (LANG_DIR / "languages.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return index


def find_go2rtc():
    """Find the go2rtc program for *this* computer.

    The folder may hold binaries for several systems (e.g. copied from a
    Windows PC to a Raspberry Pi), so only names that match this platform
    are accepted.
    """
    names = [local_go2rtc_name()]
    asset = go2rtc_asset()
    if asset:
        base = asset[:-4] if asset.endswith(".zip") else asset
        names.append(base + (".exe" if os.name == "nt" else ""))
    for name in names:
        path = ROOT / name
        if path.is_file():
            return str(path)
    return shutil.which("go2rtc")


def download_go2rtc():
    asset = go2rtc_asset()
    if not asset:
        print(f"ERROR: no go2rtc build known for {platform.system()} {platform.machine()}.")
        print("       Download it manually from https://github.com/AlexxIT/go2rtc/releases")
        return None

    url = GO2RTC_RELEASES + asset
    target = ROOT / local_go2rtc_name()
    print(f"Downloading {url} ...")
    try:
        with urllib.request.urlopen(url, timeout=60) as res:
            data = res.read()
    except OSError as e:
        print(f"ERROR: download failed: {e}")
        return None

    if asset.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            member = next((n for n in z.namelist() if Path(n).name in ("go2rtc", "go2rtc.exe")), None)
            if not member:
                print("ERROR: go2rtc program not found inside the downloaded zip")
                return None
            data = z.read(member)

    target.write_bytes(data)
    if os.name != "nt":
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"Saved {target.name} ({len(data) // (1024 * 1024)} MB)")
    return str(target)


def ensure_executable(exe):
    # Files copied from Windows (USB stick, network share) lose the exec bit.
    if os.name != "nt" and not os.access(exe, os.X_OK):
        try:
            os.chmod(exe, os.stat(exe).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except OSError:
            pass


def run_go2rtc(exe, go2rtc_env):
    args = [exe, "-config", str(GO2RTC_CONFIG)]
    env = {**os.environ, **go2rtc_env}
    os.chdir(ROOT)
    if os.name != "nt":
        # Replace this process so Ctrl+C and systemd stop go2rtc directly.
        os.execve(exe, args, env)
    try:
        return subprocess.call(args, env=env)
    except KeyboardInterrupt:
        return 0


def main():
    args = sys.argv[1:]
    if any(a in ("--help", "-h", "/?", "help") for a in args):
        try:
            # Polish letters: Windows pipes/redirects default to a legacy code page.
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
        print(HELP)
        return 0
    unknown = [a for a in args if a not in ("--check", "--download")]
    if unknown:
        print(f"Unknown option: {' '.join(unknown)}. See:  python camwall.py --help")
        return 1
    if "--download" in args:
        return 0 if download_go2rtc() else 1

    check_only = "--check" in args
    try:
        env = load_env(ENV_FILE)
        cameras, go2rtc_env, settings = load_cameras(env)
    except ConfigError as e:
        print(f"ERROR: {e}")
        return 1

    print(f"OK: {len(cameras)} camera(s): " + ", ".join(f"{c['id']} ({c['name']})" for c in cameras))
    write_page_config(cameras)
    languages = build_language_index()
    if languages:
        print("OK: languages: " + ", ".join(lang["label"] for lang in languages))
    write_go2rtc_config(cameras, webrtc_candidates(settings))

    if check_only:
        return 0

    exe = find_go2rtc()
    if not exe and sys.stdin.isatty():
        answer = input(f"go2rtc ({go2rtc_asset() or 'unknown build'}) is not installed. Download it now? [Y/n] ")
        if answer.strip().lower() in ("", "y", "yes", "t", "tak"):
            exe = download_go2rtc()
    if not exe:
        print("ERROR: go2rtc not found. Run:  python camwall.py --download")
        print("       or put the go2rtc program for this system in")
        print(f"       {ROOT}")
        return 1

    ensure_executable(exe)
    print(f"Open http://localhost:{PORT} (or http://<this computer's IP>:{PORT}). Press Ctrl+C to stop.")
    return run_go2rtc(exe, go2rtc_env)


if __name__ == "__main__":
    sys.exit(main())
