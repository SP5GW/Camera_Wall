#!/usr/bin/python3
"""
TH16-R2: read the main weather station and two RF sensors with TinyTuya.

HOW THE PROGRAM WORKS
---------------------
0. main() calls load_settings() to read th16_config.json (your own file).
1. main() calls load_devices() to read devices.json (made by the wizard).
2. main() opens one Wi-Fi connection to the TH16-R2.
3. read_main_station() reads the main station.
4. read_rf_sensor() tries each wireless sensor through the station.
5. If --cloud was selected, read_missing_from_cloud() tries any RF
   sensors that could not be read locally.

Each successful reading goes through the SAME two functions:
    show_readings() -> decode_readings()

The program only READS status. It does not control or change devices.
Keep devices.json and tinytuya.json private: they contain secret keys.
Do not edit them by hand - the wizard overwrites them. Put your own
settings (IP address, protocol version, sensor names) in th16_config.json.

WINDOWS EXAMPLES
----------------
    py -m pip install tinytuya
    py th16_all_sensors.py
    py th16_all_sensors.py --cloud
    py th16_all_sensors.py --help
"""

import argparse  # Lets you specify --ip, --cloud, etc. in PowerShell.
import json      # Reads the JSON configuration file.
import sys       # Lets main() return an exit code.
from pathlib import Path  # Makes paths work on Windows or other systems.

import tinytuya  # Library that communicates with your Tuya devices.


# ------------------------------------------------------------
# SETTINGS (change them in th16_config.json, not here)
# ------------------------------------------------------------

# Used only if th16_config.json is missing or does not set them.
STATION_IP = "192.168.1.101"
TUYA_VERSION = 3.5

# Look for JSON files next to THIS Python script by default.
SCRIPT_FOLDER = Path(__file__).resolve().parent
SETTINGS_FILE = SCRIPT_FOLDER / "th16_config.json"
DEFAULT_DEVICES_FILE = SCRIPT_FOLDER / "devices.json"
CLOUD_SETTINGS_FILE = SCRIPT_FOLDER / "tinytuya.json"


# ---------------------------------------------
# STEP 0: READ YOUR OWN SETTINGS (th16_config.json)
# ---------------------------------------------

def load_settings(filename):
    """Return the settings dictionary, or an empty one if the file is missing."""

    if not Path(filename).is_file():
        return {}

    with open(filename, "r", encoding="utf-8") as file:
        return json.load(file)


# ------------------------------------------------
# STEP 1: FIND THE WEATHER STATION AND ITS SENSORS
# ------------------------------------------------

def load_devices(filename):
    """Return (main_station, rf_sensors) from TinyTuya's devices.json."""

    with open(filename, "r", encoding="utf-8") as file:
        all_devices = json.load(file)

    main_station = None

    # Find the single TH16-R2 that is NOT a child device.
    for device in all_devices:
        is_th16 = device.get("model") == "TH16-R2"
        is_child = device.get("sub", False)

        if is_th16 and not is_child:
            if main_station is not None:
                raise ValueError("More than one TH16-R2 main station found")
            main_station = device

    if main_station is None:
        raise ValueError("No TH16-R2 main station found in devices.json")

    rf_sensors = []

    # Collect only sensors belonging to THIS main station.
    for device in all_devices:
        is_child = device.get("sub", False)
        belongs_to_station = device.get("parent") == main_station["id"]

        if is_child and belongs_to_station:
            rf_sensors.append(device)

    return main_station, rf_sensors


# ---------------------------------------
# STEP 2: CONVERT RAW DPS INTO READINGS
# ---------------------------------------

def decode_readings(raw_dps, device_info):
    """Use the device's DPS mapping to convert raw values into units."""

    readings = {}  # Start with an empty result dictionary.
    mapping = device_info.get("mapping", {})

    # Example raw_dps: {"1": 102, "2": 66}
    # Example mapping: "1" means temperature, and its scale is 1.

    for dp_number in mapping:
        dp_info = mapping[dp_number]
        measurement = dp_info.get("code")

        # We only want temperature, humidity and battery level.
        if measurement not in (
            "va_temperature",
            "va_humidity",
            "battery_percentage",
        ):
            continue

        # The station might not report every datapoint.
        if dp_number not in raw_dps:
            continue

        raw_value = raw_dps[dp_number]

        # Avoid interpreting True/False or text as measurements.
        if type(raw_value) not in (int, float):
            continue

        # Split the original compact expression into simple steps:
        #   info.get("values", {}).get("scale", 0)
        values_info = dp_info.get("values", {})
        scale = int(values_info.get("scale", 0))

        # scale 1: 102 / 10 = 10.2 degrees C
        # scale 0:  66 /  1 = 66 percent humidity
        converted_value = raw_value / (10 ** scale)
        readings[measurement] = converted_value

    return readings


# ------------------------------------
# STEP 3: PRINT THE READINGS TO SCREEN
# ------------------------------------

def show_readings(device_name, raw_dps, device_info, source):
    """Display decoded measurements. Return True if any were found."""

    readings = decode_readings(raw_dps, device_info)

    print()
    print("Device:", device_name)
    print("Source:", source)

    if "va_temperature" in readings:
        temperature = readings["va_temperature"]
        print(f"Temperature: {temperature:.1f} °C")

    if "va_humidity" in readings:
        humidity = readings["va_humidity"]
        print(f"Humidity: {humidity:.0f} %")

    if "battery_percentage" in readings:
        battery = readings["battery_percentage"]
        print(f"Battery: {battery:.0f} %")

    if len(readings) == 0:
        print("No mapped temperature, humidity or battery values found.")

    # Helpful for diagnostics: print measurements, but NOT secret keys.
    print("Raw DPS:", json.dumps(raw_dps, ensure_ascii=False, sort_keys=True))

    return len(readings) > 0


# ---------------------------------------------------
# STEP 4: EXTRACT DPS FROM A LOCAL (Wi-Fi) RESPONSE
# ---------------------------------------------------

def get_local_dps(response):
    """Return the 'dps' dictionary from a TinyTuya LAN response."""

    if not isinstance(response, dict):
        return {}

    raw_dps = response.get("dps")

    if not isinstance(raw_dps, dict):
        return {}

    return raw_dps


# ------------------------------------------------------
# STEP 5: CONVERT A CLOUD RESPONSE INTO THE SAME DPS FORM
# ------------------------------------------------------

def get_cloud_dps(response, device_info):
    """Translate Tuya Cloud's code/value items to numbered DPS items."""

    # Local format: {"1": 102, "2": 66}
    # Cloud format: [{"code": "va_temperature", "value": 102}, ...]
    # The output of this function uses the LOCAL format.

    if not isinstance(response, dict):
        return {}

    cloud_items = response.get("result")

    if not isinstance(cloud_items, list):
        return {}

    mapping = device_info.get("mapping", {})
    raw_dps = {}

    for cloud_item in cloud_items:
        if not isinstance(cloud_item, dict):
            continue

        if "value" not in cloud_item:
            continue

        cloud_code = cloud_item.get("code")

        # Find which numbered DPS matches the cloud measurement name.
        for dp_number in mapping:
            dp_info = mapping[dp_number]

            if dp_info.get("code") == cloud_code:
                raw_dps[str(dp_number)] = cloud_item["value"]
                break

    return raw_dps


# ------------------------------------------
# STEP 6: READ THE MAIN STATION OVER Wi-Fi
# ------------------------------------------

def read_main_station(gateway, main_station):
    """Request main-station status and print its temperature/humidity."""

    print("\nReading main weather station...")

    try:
        response = gateway.status()  # Ask TH16-R2 for measurements.
        raw_dps = get_local_dps(response)

        if len(raw_dps) == 0:
            print("No DPS received from the main station.")
            return False

        return show_readings(
            device_name=main_station["name"],
            raw_dps=raw_dps,
            device_info=main_station,
            source="LAN",
        )

    except Exception as error:
        print("Cannot read main station:", error)
        return False


# ---------------------------------------------------
# STEP 7: TRY TO READ ONE RF SENSOR THROUGH THE STATION
# ---------------------------------------------------

def read_rf_sensor(gateway, sensor_info):
    """Return True only if an RF sensor supplies mapped measurements."""

    sensor_name = sensor_info.get("name", "Unnamed RF sensor")
    sensor_node_id = sensor_info.get("node_id")

    print("\nReading RF sensor:", sensor_name)

    if not sensor_node_id:
        print("Cannot query this sensor locally: node_id is missing.")
        return False

    try:
        # The sensor has no Wi-Fi IP of its own. We ask through its parent.
        sensor = tinytuya.Device(
            sensor_info["id"],
            parent=gateway,
            cid=sensor_node_id,
        )

        response = sensor.status()
        raw_dps = get_local_dps(response)

        if len(raw_dps) == 0:
            print("RF sensor did not return DPS over the LAN.")
            return False

        return show_readings(
            device_name=sensor_name,
            raw_dps=raw_dps,
            device_info=sensor_info,
            source="LAN via TH16-R2",
        )

    except Exception as error:
        print("Cannot read this RF sensor locally:", error)
        return False


# --------------------------------------------
# STEP 8 (OPTIONAL): TRY MISSING SENSORS IN CLOUD
# --------------------------------------------

def read_missing_from_cloud(missing_sensors):
    """Read unavailable RF sensors using Tuya Cloud status queries."""

    if not CLOUD_SETTINGS_FILE.is_file():
        print("\nCloud settings file not found:", CLOUD_SETTINGS_FILE)
        print("Run 'py -m tinytuya wizard' to configure cloud access.")
        return

    try:
        cloud = tinytuya.Cloud(configFile=str(CLOUD_SETTINGS_FILE))
    except Exception as error:
        print("Cannot start Tuya Cloud access:", error)
        return

    for sensor_info in missing_sensors:
        sensor_name = sensor_info.get("name", "Unnamed RF sensor")
        print("\nReading from Tuya Cloud:", sensor_name)

        try:
            response = cloud.getstatus(sensor_info["id"])
            raw_dps = get_cloud_dps(response, sensor_info)

            if len(raw_dps) == 0:
                print("Cloud did not return mapped readings for this sensor.")
                continue

            show_readings(
                device_name=sensor_name,
                raw_dps=raw_dps,
                device_info=sensor_info,
                source="Tuya Cloud",
            )

        except Exception as error:
            print("Cloud request failed:", error)


# -------------------------------------
# STEP 9: RUN EVERYTHING IN THE RIGHT ORDER
# -------------------------------------

def main():
    """Load configuration, read main station, read children, optionally cloud."""

    # STEP 0: Read your own settings. Command-line options override them.
    try:
        settings = load_settings(SETTINGS_FILE)
    except (OSError, json.JSONDecodeError) as error:
        print("Cannot read th16_config.json:", error)
        return 2

    # argparse reads optional settings you type in PowerShell.
    parser = argparse.ArgumentParser(description="Read TH16-R2 and two RF sensors")
    parser.add_argument("--devices", default=str(DEFAULT_DEVICES_FILE))
    parser.add_argument("--ip", default=settings.get("station_ip", STATION_IP))
    parser.add_argument(
        "--version",
        type=float,
        default=settings.get("protocol_version", TUYA_VERSION),
    )
    parser.add_argument("--cloud", action="store_true")
    options = parser.parse_args()

    # STEP 1: Load device information from devices.json.
    try:
        main_station, rf_sensors = load_devices(options.devices)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print("Cannot read devices.json:", error)
        return 2

    # Use your own names instead of the app's names.
    if "station_name" in settings:
        main_station["name"] = settings["station_name"]

    # RF sensors are matched by their node_id.
    sensor_names = settings.get("sensor_names", {})
    for sensor_info in rf_sensors:
        node_id = sensor_info.get("node_id")
        if node_id in sensor_names:
            sensor_info["name"] = sensor_names[node_id]

    if not main_station.get("id") or not main_station.get("key"):
        print("The TH16-R2 Device ID or Local Key is missing in devices.json.")
        return 2

    print("Main station:", main_station.get("name", "TH16-R2"))
    print("IP address:", options.ip)
    print("Protocol version:", options.version)
    print("Number of RF sensors:", len(rf_sensors))

    # STEP 2: Create a connection to the Wi-Fi main station.
    # The RF sensors will reuse this gateway connection.
    gateway = tinytuya.Device(
        main_station["id"],
        options.ip,
        main_station["key"],
        version=options.version,
        persist=True,
    )
    gateway.set_socketTimeout(7)

    missing_sensors = []

    try:
        # STEP 3: Read main station.
        read_main_station(gateway, main_station)

        # STEP 4: Try each RF sensor locally.
        for sensor_info in rf_sensors:
            success = read_rf_sensor(gateway, sensor_info)

            if not success:
                missing_sensors.append(sensor_info)

    finally:
        # Always close the TCP connection, including after errors.
        gateway.close()

    # STEP 5: Only if requested, try missing RF sensors in cloud.
    if len(missing_sensors) == 0:
        print("\nAll configured RF sensors returned local measurements.")
    elif options.cloud:
        read_missing_from_cloud(missing_sensors)
    else:
        print("\nSome RF readings were unavailable locally.")
        print("Use --cloud to try their Tuya Cloud readings.")
        print("An unavailable reading does not prove RF local access is unsupported.")

    return 0


# Python starts here when you run this file from PowerShell.
if __name__ == "__main__":
    sys.exit(main())
