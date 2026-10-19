"""Weather readings for CamWall: the TH16-R2 station and its RF sensors.

A background thread reads the station and the wireless sensors paired with
it, and writes www/weather.json, which the page shows in the top bar.

Each device is read over the home network (LAN) first. Devices that do not
answer locally are read from Tuya Cloud instead (if th16/tinytuya.json
exists), but not more often than every cloud_refresh_seconds, to stay within
the free Tuya Cloud quota. A device that cannot be read at all keeps its
last reading, marked as old on the page.

Decoding is shared with the stand-alone reader th16/th16_all_sensors.py.
Settings are in th16/th16_config.json (see README.md).

weather.json contains only readings and names - no keys or passwords.
"""

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TH16_DIR = ROOT / "th16"
DEVICES_FILE = TH16_DIR / "devices.json"
SETTINGS_FILE = TH16_DIR / "th16_config.json"
CLOUD_FILE = TH16_DIR / "tinytuya.json"
WEATHER_FILE = ROOT / "www" / "weather.json"

DEFAULT_IP = "192.168.1.101"
DEFAULT_VERSION = 3.5
REFRESH_SECONDS = 60
CLOUD_REFRESH_SECONDS = 300
MIN_REFRESH_SECONDS = 10
LAN_TIMEOUT = 5  # seconds per connection attempt

try:
    import tinytuya
    from th16 import th16_all_sensors as th16
except ImportError as e:
    tinytuya = th16 = None
    IMPORT_ERROR = e

SOURCE_TEXT = {"lan": "LAN", "cloud": "Tuya Cloud"}


class WeatherError(Exception):
    pass


def now_iso(t=None):
    return datetime.fromtimestamp(time.time() if t is None else t).astimezone().isoformat(timespec="seconds")


def write_json(path, data):
    """Replace the file in one step, so the page never reads half of it."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for _ in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: go2rtc is sending the old file right now
            time.sleep(0.1)
    os.replace(tmp, path)


def remove_weather_file():
    """Without weather.json the page hides the weather display."""
    try:
        WEATHER_FILE.unlink()
    except FileNotFoundError:
        pass


def load_weather(mode):
    """Return a Weather reader, or None if the weather display is off.

    mode "auto": use the station if th16/devices.json exists (warn if it can't be used),
         "on":   the station must work (WeatherError otherwise),
         "off":  no weather.
    """
    if mode == "off":
        print("OK: weather: off (settings in cameras.yaml)")
        return None
    if mode == "auto" and not DEVICES_FILE.exists():
        print("OK: weather: th16/devices.json not found - no weather station")
        return None
    try:
        if tinytuya is None:
            raise WeatherError(f"TinyTuya is missing ({IMPORT_ERROR}). Install it with:  python -m pip install tinytuya")
        return Weather()
    except WeatherError as e:
        if mode == "on":
            raise
        print(f"WARNING: weather: {e} - weather not shown")
        return None


def positive_number(settings, key, default, minimum):
    value = settings.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < minimum:
        raise WeatherError(f"th16_config.json: '{key}' must be a number, at least {minimum} (found {value!r})")
    return value


class Weather:
    def __init__(self):
        try:
            settings = th16.load_settings(SETTINGS_FILE)
        except (OSError, json.JSONDecodeError) as e:
            raise WeatherError(f"cannot read th16/th16_config.json: {e}")
        if not isinstance(settings, dict):
            raise WeatherError("th16/th16_config.json must contain { \"key\": value } settings")
        try:
            station, sensors = th16.load_devices(DEVICES_FILE)
        except (OSError, ValueError) as e:  # JSONDecodeError is a ValueError
            raise WeatherError(f"cannot use th16/devices.json: {e}")
        if not station.get("id") or not station.get("key"):
            raise WeatherError("the TH16-R2 Device ID or Local Key is missing in th16/devices.json")

        # Your own names from th16_config.json, as in th16_all_sensors.py.
        if "station_name" in settings:
            station["name"] = settings["station_name"]
        names = settings.get("sensor_names", {})
        for sensor in sensors:
            if sensor.get("node_id") in names:
                sensor["name"] = names[sensor["node_id"]]
            sensor.setdefault("name", "RF sensor")
        station.setdefault("name", "TH16-R2")

        self.station = station
        self.sensors = sorted(sensors, key=lambda s: s["name"])
        self.devices = [station] + self.sensors
        self.ip = settings.get("station_ip", DEFAULT_IP)
        self.version = positive_number(settings, "protocol_version", DEFAULT_VERSION, 3.1)
        self.refresh = positive_number(settings, "refresh_seconds", REFRESH_SECONDS, MIN_REFRESH_SECONDS)
        self.cloud_refresh = positive_number(settings, "cloud_refresh_seconds", CLOUD_REFRESH_SECONDS, 60)
        self.use_cloud = settings.get("cloud_fallback", True) is not False and CLOUD_FILE.is_file()
        # A reading older than this is shown as old on the page.
        self.stale_after = round(max(3 * self.refresh, 2 * self.cloud_refresh if self.use_cloud else 0))

        self.cloud = None
        self.last_cloud = 0.0
        self.last = {}    # device id -> {"readings", "source", "time"}
        self.shown = {}   # device id -> what was last printed (to print only changes)

    def describe(self):
        cloud = f", Tuya Cloud fallback every {self.cloud_refresh:g} s" if self.use_cloud else ", no cloud fallback"
        names = ", ".join(d["name"] for d in self.devices)
        return f"{names} (station {self.ip}, every {self.refresh:g} s{cloud})"

    # ---------- reading ----------

    def _keep(self, got, device, raw_dps, source):
        readings = th16.decode_readings(raw_dps, device)
        if readings:
            got[device["id"]] = (readings, source)

    def _read_lan(self, got):
        gateway = tinytuya.Device(self.station["id"], self.ip, self.station["key"],
                                  version=self.version, persist=True, connection_timeout=LAN_TIMEOUT)
        gateway.set_socketRetryLimit(1)  # retries happen on the next round anyway
        try:
            response = gateway.status()
            if isinstance(response, dict) and "Err" in response:
                return  # station not reachable over LAN, so its RF sensors aren't either
            self._keep(got, self.station, th16.get_local_dps(response), "lan")
            for sensor in self.sensors:
                if not sensor.get("node_id"):
                    continue
                # The sensor has no Wi-Fi of its own: ask through the station.
                child = tinytuya.Device(sensor["id"], parent=gateway, cid=sensor["node_id"])
                self._keep(got, sensor, th16.get_local_dps(child.status()), "lan")
        except Exception:
            pass  # whatever was not read is tried in Tuya Cloud
        finally:
            gateway.close()

    def _read_cloud(self, devices, got):
        try:
            if self.cloud is None:
                self.cloud = tinytuya.Cloud(configFile=str(CLOUD_FILE))
            for device in devices:
                self._keep(got, device, th16.get_cloud_dps(self.cloud.getstatus(device["id"]), device), "cloud")
        except Exception as e:
            self.cloud = None  # start a fresh cloud session next time
            print(f"WARNING: weather: Tuya Cloud request failed: {e}")

    def poll(self):
        """Read every device once and write weather.json."""
        got = {}
        self._read_lan(got)
        missing = [d for d in self.devices if d["id"] not in got]
        now = time.time()
        if missing and self.use_cloud and now - self.last_cloud >= self.cloud_refresh:
            self.last_cloud = now
            self._read_cloud(missing, got)
        now = time.time()
        for device_id, (readings, source) in got.items():
            self.last[device_id] = {"readings": readings, "source": source, "time": now}
        self._print_changes()
        write_json(WEATHER_FILE, self.snapshot())

    def _print_changes(self):
        for device in self.devices:
            last = self.last.get(device["id"])
            if last:
                line = self._reading_text(last)
            elif self.use_cloud:
                line = "no reading - not reachable over LAN nor in Tuya Cloud"
            else:
                line = "no reading - not reachable over LAN (Tuya Cloud fallback is off)"
            # Print a device only when its source or reachability changes, not every round.
            key = last["source"] if last else None
            if self.shown.get(device["id"]) != key:
                self.shown[device["id"]] = key
                print(f"{'OK' if last else 'WARNING'}: weather: {device['name']}: {line}")

    @staticmethod
    def _reading_text(last):
        r = last["readings"]
        parts = []
        if "va_temperature" in r:
            parts.append(f"{r['va_temperature']:.1f} °C")
        if "va_humidity" in r:
            parts.append(f"{r['va_humidity']:.0f} %")
        if "battery_percentage" in r:
            parts.append(f"battery {r['battery_percentage']:.0f} %")
        return ", ".join(parts) + f" ({SOURCE_TEXT[last['source']]})"

    def snapshot(self):
        now = time.time()
        devices = []
        for device in self.devices:
            last = self.last.get(device["id"])
            r = last["readings"] if last else {}
            devices.append({
                "name": device["name"],
                "main": device is self.station,
                "temperature": r.get("va_temperature"),
                "humidity": r.get("va_humidity"),
                "battery": r.get("battery_percentage"),
                "source": last["source"] if last else None,
                "time": now_iso(last["time"]) if last else None,
                "stale": not last or now - last["time"] > self.stale_after,
            })
        return {"updated": now_iso(now), "refresh": self.refresh, "staleAfter": self.stale_after, "devices": devices}

    # ---------- background thread ----------

    def start(self):
        """Write the names at once (the page shows them), then read in the background."""
        write_json(WEATHER_FILE, self.snapshot())
        threading.Thread(target=self._run, name="weather", daemon=True).start()

    def _run(self):
        while True:
            try:
                self.poll()
            except Exception as e:  # never let a surprise stop the readings for good
                print(f"WARNING: weather: {e}")
            time.sleep(self.refresh)
