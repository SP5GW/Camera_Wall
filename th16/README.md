> **Part of CamWall.** These files feed the temperature display of the camera wall
> (`../weather.py` reads them). The step-by-step setup is in `../README.md`,
> section *2. Configure the weather station*, including all keys of
> `th16_config.json`. This file is the original reference for the station and
> the TinyTuya commands. Run the TinyTuya wizard **inside this `th16` folder**,
> so that `devices.json` and `tinytuya.json` are written here.

# Programming TH16 Weather Station with Python and TinyTuya

This project provides a step-by-step guide to discovering and configuring the TH16 weather station for use with Python's `tinytuya` module.  
It includes a simple Python script that reads temperature and humidity data from the main unit and two additional paired sensors.  
The script's architecture is described in detail in the accompanying document, [`TH16_script_architecture.pdf`](TH16_script_architecture.pdf).

## Identify IP addresses of Tuya hardware

Run a network scan:

```bash
nmap -sn 192.168.1.0/24
```

Example output:

```text
Nmap scan report for 192.168.1.101
Host is up (0.27s latency).
MAC Address: 20:F1:B2:XX:XX:XX (Tuya Smart)
```

## Confirm that Tuya device control port 6668 is open

Run:

```bash
nmap -Pn -p 6668 192.168.1.101
```

Example output:

```text
Starting Nmap 7.992 (https://nmap.org) at 2026-10-17 16:24 +0200
Nmap scan report for 192.168.1.101
Host is up (0.086s latency).

PORT     STATE SERVICE
6668/tcp open  irc
MAC Address: 20:F1:B2:XX:XX:XX (Tuya Smart)

Nmap done: 1 IP address (1 host up) scanned in 0.97 seconds
```

> **Note:** Nmap may label port 6668 as `irc` based on its service database. That label does not determine which application protocol the device actually uses.

## Get the Device ID and confirm the MAC address

In the **Smart Life** or **Tuya Smart** mobile app:

1. Open your weather station.
2. Tap **⋯** (top-right corner).
3. Select **Device Information**.
4. Copy the **Virtual ID** — this is your **Device ID**.
5. Copy the **MAC address** and cross-check it against the Nmap scan results.

Example values (masked for public sharing):

```text
MAC:       20:F1:B2:XX:XX:XX
Device ID: YOUR_DEVICE_ID
```

## Get the Local Key

1. Open the [Tuya IoT Platform](https://www.tuya.com/) and create a developer account.
2. Go to **Cloud → Development → Create Cloud Project**.
3. Choose **Smart Home** and the appropriate data center (typically Central Europe for Poland).
4. Open your project and go to **Devices → Link Tuya App Account**.
5. Scan the displayed QR code using **Smart Life** or **Tuya Smart**.
6. Find the **Access ID** and **Access Secret** under the project's **Overview**.

Under **Project → Authorization**, locate the authorization credentials:

```text
Authorization Key: YOUR_AUTHORIZATION_KEY
Access Secret:     YOUR_ACCESS_SECRET
```

**Security:** Do not publish real authorization keys, access secrets, Local Keys, or device configuration files containing credentials in a public GitHub repository.

## Obtain device configuration using the TinyTuya wizard

Run:

```bash
py -m tinytuya wizard
```

Provide the following values when prompted:

1. **Authorization/API key**
2. **Access Secret**
3. **Region:** `eu` (must match the app and developer account)
4. **Device ID**

The wizard produces up to four files:

| File | Contents | When created |
| --- | --- | --- |
| `tinytuya.json` | Tuya Cloud API credentials, region, and Device ID | During configuration |
| `devices.json` | Registered devices, Device IDs, Local Keys, names, and DPS mappings | After successful cloud retrieval |
| `tuya-raw.json` | Complete raw response from Tuya Cloud | After successful cloud retrieval |
| `snapshot.json` | Discovered IP addresses, protocol versions, and device status | If you answer **Y** to polling |

From `devices.json`, the weather station hardware model is **TH16-R2**.

### Which files to edit (and which not)

| File | Who writes it | Edit by hand? | In Git? |
| --- | --- | --- | --- |
| `devices.json`, `tinytuya.json`, `tuya-raw.json`, `snapshot.json` | TinyTuya wizard | No — the wizard overwrites them | No (contain secrets) |
| `devices.example.json`, `tinytuya.example.json` | You (sanitized copies) | Yes | Yes |
| `th16_config.json` | You | Yes — station IP, protocol version, sensor names | Yes |

Other Tuya devices on the same account (for example a power plug) can stay in `devices.json`: the script only uses the TH16-R2 and the RF sensors paired with it, so there is no need to remove them by hand.

## Run the script

```bash
py th16_all_sensors.py           # read the station and RF sensors over LAN
py th16_all_sensors.py --cloud   # also try Tuya Cloud for sensors that did not answer locally
py th16_all_sensors.py --help    # all options (--ip and --version override th16_config.json)
```

## Find the Tuya protocol version

Determine the supported version by trying the following commands:

```bash
py -m tinytuya get --id YOUR_DEVICE_ID --ip 192.168.1.101 --version 3.3
py -m tinytuya get --id YOUR_DEVICE_ID --ip 192.168.1.101 --version 3.4
py -m tinytuya get --id YOUR_DEVICE_ID --ip 192.168.1.101 --version 3.5
```

In this case, version **3.5** returned valid **DPS** data.

## Summary of basic TinyTuya commands

**Display TinyTuya help:**

```bash
py -m tinytuya --help
```

**Discover Tuya devices** (does not always work):

```bash
py -m tinytuya scan
```

**Configure cloud credentials and retrieve Local Keys:**

```bash
py -m tinytuya wizard
```

**List configured devices:**

```bash
py -m tinytuya list
```

**Show options for reading a device:**

```bash
py -m tinytuya get --help
```

**Read device data:**

```bash
py -m tinytuya get --id YOUR_DEVICE_ID --ip 192.168.1.101 --version 3.5
```
