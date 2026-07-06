# `screen.py` – AtomMan Serial Display Daemon

`screen.py` is a Python daemon that drives the **AtomMan USB serial display panel**, unlocking it after boot and continuously updating all tiles with system metrics (CPU, GPU, memory, disk, date, weather, network, volume, battery, and fan speed).

The script is designed to run as a **systemd service** on Linux, with optional console dashboard output for debugging.

---

## Features

- **Unlock & Retry Logic** – Robust startup handshake with retries until the panel activates.
- **Per-Tile Payloads** – Updates CPU, GPU, memory, disk, date, weather, net, volume, battery.
- **Fan Speed** (with caching & smoothing)
  - Primary: Linux **hwmon** (`/sys/class/hwmon/*/fan*_input`) – returns actual RPM
  - Fallback: NVIDIA via **pynvml** (fast) or `nvidia-smi` (subprocess) – converts % to RPM
  - Final fallback: `-1` if no source found
  - 2-second caching to reduce syscalls
  - Rolling average smoothing (last 5 readings)
- **CPU Metrics** (cached & optimized)
  - Model cached after first read
  - Usage calculated from `/proc/stat` deltas (non-blocking, 0.5s TTL)
  - Temperature prioritizes CPU-specific sensors (coretemp, k10temp, zenpower)
- **Disk Temperature** – Reads from NVMe hwmon, drivetemp, smartctl, or hddtemp
- **CPU Frequency in kHz** (panel requires kHz, not MHz)
- **Date/Time/Week/Weather Tile**
  - Week uses `0..6` where `0=Sunday … 6=Saturday`
  - Full payload form:
    ```
    {Date:YYYY/MM/DD;Time:HH:MM:SS;Week:N;Weather:X;TemprLo:L,TemprHi:H,Zone:Z,Desc:D}
    ```
  - `Weather` is a numeric code (1–40) selecting an icon baked into the panel firmware.
  - `Zone` and `Desc` fields exist in the protocol but the panel firmware ignores them.
- **Network Throughput Auto-Scaling**
  - Values shown in `K/s`, `M/s`, or `G/s` depending on rate
- **Optional Console Dashboard** (`--dashboard`) with ANSI colors
- **Configurable Start Delay** (ensures drivers/fans are ready before panel comms start)
- **Graceful Shutdown** – Handles SIGTERM and SIGINT for clean systemd stops
- **Auto-Reconnect** – Detects a dead serial link (stale fd after sleep/wake, USB drop) and reopens the port + re-runs the unlock handshake in-process, no reboot needed
- **Systemd Ready** – run as a background service with restart policy.

---

## Requirements

- **Python 3.10+**
- **Linux** with `/proc`, `/sys` filesystems

### Required Dependencies

| Package | Purpose |
|---------|---------|
| `pyserial` | Serial communication with the panel |

### Optional Dependencies

| Package | Purpose |
|---------|---------|
| `nvidia-ml-py` | Fast GPU fan/temp queries (recommended for NVIDIA GPUs) |
| `smartmontools` | Disk temperature via `smartctl` (SATA drives) |
| `hddtemp` | Alternative disk temperature source |

### System Packages

```bash
sudo apt update
sudo apt install python3 python3-pip dmidecode pciutils lshw
```

### Python Packages

```bash
pip install pyserial

# Optional but recommended for NVIDIA GPUs (10x faster than nvidia-smi):
pip install nvidia-ml-py
```

> **Note:** `nvidia-ml-py` provides the `pynvml` module for direct GPU queries (~1ms) instead of spawning `nvidia-smi` subprocess (~17ms). The script auto-detects and uses it if available.

---

## Permissions

The AtomMan display is exposed as a USB serial device under `/dev/serial/by-id/...`.
Grant your user access by adding them to the `dialout` group:

```bash
sudo usermod -aG dialout <YOUR_USER>
```

Log out and back in for this to take effect.

---

## Usage

Run directly:

```bash
python3 screen.py --dashboard
```

### Command-line Flags

| Flag             | Default   | Description                                                                 |
|------------------|-----------|-----------------------------------------------------------------------------|
| `--attempts`     | `3`       | Unlock attempts before falling back into passive mode.                      |
| `--window`       | `5.0`     | Seconds per unlock attempt window.                                          |
| `--start-delay`  | `3.0`     | Seconds to sleep before opening serial port (driver warm-up).               |
| `--dashboard`    | *off*     | Show live dashboard in console.                                             |
| `--no-color`     | *off*     | Disable ANSI colors in dashboard.                                           |
| `--fan-prefer`   | `auto`    | `auto` → hwmon → nvidia; or force one (`hwmon` or `nvidia`).                |
| `--fan-max-rpm`  | `2000`    | Max RPM for NVIDIA % → RPM conversion (see note below).                     |

### Environment Variables

All flags can be set via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `ATOMMAN_PORT` | `/dev/serial/by-id/usb-Synwit_USB_Virtual_COM-if00` | Serial port path |
| `ATOMMAN_BAUD` | `115200` | Baud rate |
| `ATOMMAN_FAN_PREFER` | `auto` | Fan source preference |
| `ATOMMAN_FAN_MAX_RPM` | `2000` | Max RPM for NVIDIA conversion |
| `ATOMMAN_WEATHER_REFRESH` | `600` | Weather cache refresh (seconds) |
| `ATOMMAN_LINK_TIMEOUT` | `15` | Seconds of ENQ silence before the link is treated as dead and the port reopened |

### Fan Speed Note

**NVIDIA GPUs only report fan speed as a percentage (0-100%)**, not actual RPM. The script estimates RPM as:

```
RPM = percentage × max_rpm / 100
```

With the default `--fan-max-rpm=2000`:
- 30% → 600 RPM
- 50% → 1000 RPM
- 100% → 2000 RPM

Adjust `--fan-max-rpm` to match your GPU's actual maximum fan speed for more accurate readings. Most modern GPUs have max fan speeds between 1500-3000 RPM.

**hwmon sensors** (if available) report actual RPM directly and don't use this conversion.

---

## Weather Support

### Original Windows App
- The official Windows control software used a **private weather API** provided by the panel vendor.
- The API requires a vendor-specific key; when tested outside the Windows app, it returns:
  ```
  {"status":"You do not have access to this API.","status_code":"AP010002"}
  ```
- Because of this restriction, the Windows weather feed cannot be reused directly.

### Weather Providers
`screen.py` supports two weather sources and selects one automatically:

- **OpenWeather** – used when an API key is set in `OW_API_KEY`. Uses the One Call 3.0 endpoint (free to start, but requires a card on file).
- **Open-Meteo** – a free, **no-API-key** provider used automatically when no key is set *or* when an OpenWeather request fails. This means weather works out of the box with zero configuration.

Location (`OW_LOCATION`) can be a city name, `zip,country`, or `lat,lon`, and applies to both providers. Weather refreshes every 10 minutes by default (adjustable via `ATOMMAN_WEATHER_REFRESH`).

On success the following fields are extracted:
- **Weather condition → panel icon number (1–40)**, with day/night icon variants
- **Daily low/high temperature**
- **Condition description** and **Zone (city, country)** (used in dashboard only)

### Panel Behavior
- **Weather**: numeric code shows the corresponding icon (1=first icon, 40=last).
- **TemprLo / TemprHi**: numeric values are displayed.
- **Zone / Desc**: accepted in payload but never displayed on screen. They are "dead fields."

### Example Payloads
With weather data:
```
{Date:2025/09/15;Time:22:14:03;Week:1;Weather:4;TemprLo:12,TemprHi:27,Zone:Denver,US,Desc:broken clouds}
```

Without weather (no internet or no API key):
```
{Date:2025/09/15;Time:22:14:03;Week:1;Weather:;TemprLo:,TemprHi:,Zone:,Desc:}
```

### Dashboard Example
Dashboard shows all weather details (even Zone and Desc for clarity), but the panel itself only uses icon and temps:

```
Weather        : ONLINE
Code           : 1 (mapped)
Lo/Hi          : 12/28 °C
Zone           : Denver, Colorado, US
Desc           : clear sky
Age            : 4s (refresh 600s)
```

---

## OpenWeather → Panel Icon Mapping

The actual mapping lives in `_map_openweather_id_to_weatherN()` in `screen.py`. Codes are chosen to match icons baked into the panel firmware. Open-Meteo uses WMO condition codes, mapped to the same panel codes by `_map_wmo_to_weatherN()`.

| OpenWeather condition (id)              | Panel code |
|------------------------------------------|------------|
| Clear sky (800) — day / night            | 1 / 3      |
| Few clouds (801) — day / night           | 5 / 6      |
| Scattered clouds (802) — day / night     | 7 / 8      |
| Broken / overcast clouds (803, 804)      | 9          |
| Thunderstorm (2xx)                       | 11         |
| Severe thunderstorm (202, 212, 232)      | 16         |
| Drizzle (3xx)                            | 13         |
| Light rain (500)                         | 13         |
| Moderate rain (501)                      | 14         |
| Heavy rain (502, 503, 504)               | 15         |
| Freezing rain (511)                      | 19         |
| Shower rain (520, 521, 522, 531)         | 10         |
| Light snow (600)                         | 22         |
| Moderate snow (601)                      | 23         |
| Heavy / snow showers (602, 621, 622)     | 24         |
| Snow flurry (620)                        | 21         |
| Sleet / wintry mix (611, 612, 615, 616)  | 20         |
| Mist / fog (701, 741)                    | 30         |
| Smoke / haze (711, 721)                  | 31         |
| Sand (731, 751)                          | 27         |
| Dust / volcanic ash (761, 762)           | 26         |
| Squalls (771)                            | 33         |
| Tornado (781)                            | 36         |
| Unmapped                                 | 99         |

---

## Example Dashboard

```
AtomMan — Active   Time: 2025-09-15 13:14:57
----------------------------------------------------------------
Processor type : Intel(R) Core(TM) Ultra 9 185H
Processor temp : 45 °C
CPU usage      : 7 %
CPU freq       : 1919081 kHz

GPU model      : NVIDIA GeForce RTX 3090
GPU temp       : 36 °C
GPU usage      : 6 %

RAM (vendor)   : Kingston
RAM used       : 6.2 GB
RAM avail      : 56.4 GB
RAM total      : 62.6 GB
RAM usage      : 10 %

Disk (label)   : ESO0001TTLCW-EP3-2L
Disk used      : 152 GB
Disk total     : 436 GB
Disk usage     : 35 %
Disk temp      : 42 °C

Net iface      : wlp89s0f0
Net RX,TX      : 1.2 K/s, 3.0 K/s
Fan speed      : 600 r/min
Volume         : 44 %
Battery        : 100 %

Weather        : ONLINE
Code           : 1 (mapped)
Lo/Hi          : 12/28 °C
Zone           : Denver, Colorado, US
Desc           : clear sky
Age            : 4s (refresh 600s)
----------------------------------------------------------------
```

---

## Systemd Service Setup

1. Copy `screen.py` into `/opt/atomman/screen.py` (or your preferred location).

2. Create a service file:

```ini
# /etc/systemd/system/atomman.service
[Unit]
Description=AtomMan Screen Daemon
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/atomman/screen.py --start-delay 5
WorkingDirectory=/opt/atomman
Restart=always
User=<YOUR_USER>
Group=dialout

[Install]
WantedBy=multi-user.target
```

3. Reload systemd and enable the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable atomman
sudo systemctl start atomman
```

4. Check logs:

```bash
journalctl -u atomman -f
```

---

## Internals

### Protocol

- **ENQ (device → host):**
  ```
  AA 05 <SEQ_ASCII> CC 33 C3 3C
  ```

- **REPLY (host → device):**
  ```
  AA <TileID> 00 <SEQ_ASCII> {ASCII payload} CC 33 C3 3C
  ```

### Tiles

| Tile | ID  | Seq | Payload Example                                                                 |
|------|-----|-----|---------------------------------------------------------------------------------|
| CPU  | 0x53| '2' | `{CPU:AMD Ryzen 9;Tempr:62;Useage:27;Freq:3900000;Tempr1:62;}`                   |
| GPU  | 0x36| '3' | `{GPU:NVIDIA RTX 4060;Tempr:70;Useage:43}`                                      |
| MEM  | 0x49| '4' | `{Memory:Samsung;Used:9.5;Available:22.3;Total:31.8;Useage:29}` (vendor or `Memory` if unknown) |
| DISK | 0x4F| '5' | `{DiskName:Samsung SSD 980 PRO;Tempr:42;UsageSpace:222;AllSpace:931;Usage:24}`  |
| DATE | 0x6B| '6' | `{Date:2025/09/15;Time:14:22:10;Week:1;Weather:;TemprLo:,TemprHi:,Zone:,Desc:}` |
| NET  | 0x27| '7' | `{SPEED:1570;NETWORK:2.4M/s,312K/s}`                                            |
| VOL  | 0x10| '9' | `{VOLUME:45}`                                                                   |
| BAT  | 0x1A| '2' | `{Battery:97}`                                                                  |

---

## Troubleshooting

- **Fan shows `-1`** → Increase `--start-delay` so NVIDIA/hwmon drivers initialize.
- **No serial access** → Add your user to `dialout` (`sudo usermod -aG dialout <YOUR_USER>`).
- **Panel does not unlock** → Increase `--window` or `--attempts`.
- **Screen blank after sleep/wake** → Handled automatically: the daemon detects the dead link and reopens the port within a few seconds. If it persists, lower `ATOMMAN_LINK_TIMEOUT`. No reboot or manual USB reset required.
- **Network RX/TX stuck** → Ensure interface is up (`ip link show`).
- **Weather blank** → No internet access. An API key is no longer required — Open-Meteo is used automatically without one.
- **Zone/Desc not showing** → Expected, panel ignores them.
- **CPU temp shows wrong sensor** → The script prioritizes coretemp/k10temp; check `sensors` output.
- **Disk temp shows 0** → Install `smartmontools` or enable `drivetemp` kernel module.

---

## Changelog

### v1.2.0

**Reliability:**
- Added in-process **auto-reconnect**. If the serial link goes dead — a stale file descriptor after system sleep/wake, or a USB drop — the daemon reopens the port and re-runs the unlock handshake automatically, recovering in a few seconds instead of staying blank until a reboot.

**Weather:**
- Added **Open-Meteo** as a free, keyless weather provider. Weather now works with no API key: the script uses OpenWeather when a key is set and falls back to Open-Meteo when no key is set or an OpenWeather request fails.
- Fixed night weather icons never appearing (the day/night flag was always read as daytime).

### v1.1.0

**Performance & Caching:**
- CPU usage now uses non-blocking `/proc/stat` delta calculation (was 80ms blocking sleep)
- CPU model cached after first read (was re-reading `/proc/cpuinfo` every cycle)
- Fan readings cached for 2 seconds with rolling average smoothing
- hwmon fan path cached after discovery (was re-globbing every call)
- Added `nvidia-ml-py` (pynvml) support for 10x faster GPU queries

**Accuracy Fixes:**
- CPU temperature now prioritizes CPU-specific drivers (coretemp, k10temp, zenpower, cpu_thermal, acpitz)
- Disk temperature now reads actual sensor (was hardcoded to 33)
- Fan max RPM default changed from 5000 to 2000 (more typical for modern GPUs)

**Bug Fixes:**
- Fixed file handle leaks (open().read() without close)
- Fixed argparse crash on Python 3.14 (unescaped % in help string)
- Added SIGTERM handler for clean systemd shutdown

**Dashboard:**
- Added disk temperature display with color coding

---

## License

MIT License — use freely for personal or commercial projects.
