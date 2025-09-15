# `screen.py` – AtomMan Serial Display Daemon

`screen.py` is a Python daemon that drives the **AtomMan USB serial display panel**, unlocking it after boot and continuously updating all tiles with system metrics (CPU, GPU, memory, disk, date, weather, network, volume, battery, and fan speed).  

The script is designed to run as a **systemd service** on Linux, with optional console dashboard output for debugging.

---

## Features

- **Unlock & Retry Logic** – Robust startup handshake with retries until the panel activates.  
- **Per-Tile Payloads** – Updates CPU, GPU, memory, disk, date, weather, net, volume, battery.  
- **Fan Speed**  
  - Primary: Linux **hwmon** (`/sys/class/hwmon/*/fan*_input`)  
  - Fallback: NVIDIA (`nvidia-smi --query-gpu=fan.speed`) → converts % to RPM  
  - Final fallback: `-1` if no source found  
- **CPU Frequency in kHz** (panel requires kHz, not MHz)  
- **Date/Time/Week/Weather Tile**  
  - Week uses `0..6` where `0=Sunday … 6=Saturday`  
  - Full payload form:  
    ```
    {Date:YYYY/MM/DD;Time:HH:MM:SS;Week:N;Weather:X;TemprLo:L,TemprHi:H,Zone:Z,Desc:D}
    ```
  - `Weather` is a numeric code (1–40) selecting an icon baked into the panel firmware.  
  - `Zone` and `Desc` fields exist in the protocol but the panel firmware ignores them (they do not render, even when ASCII text is sent).  
- **Network Throughput Auto-Scaling**  
  - Values shown in `K/s`, `M/s`, or `G/s` depending on rate  
- **Optional Console Dashboard** (`--dashboard`) with ANSI colors  
- **Configurable Start Delay** (ensures drivers/fans are ready before panel comms start)  
- **Systemd Ready** – run as a background service with restart policy.  

---

## Requirements

- Python 3.10+  
- `pyserial`  
- Linux with `/proc`, `/sys`, and `nvidia-smi` (optional for NVIDIA GPU metrics)  

Install dependencies:

```bash
sudo apt update
sudo apt install python3 python3-pip dmidecode pciutils lshw
pip install pyserial
```

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
| `--fan-prefer`   | `auto`    | `auto` → hwmon → nvidia; or force one (`hwmon` or `nvidia`).                 |
| `--fan-max-rpm`  | `5000`    | Used when NVIDIA reports % only; converted into RPM.                        |

---

## Weather Support

### Original Windows App
- The official Windows control software used a **private weather API** provided by the panel vendor.  
- The API requires a vendor-specific key; when tested outside the Windows app, it returns:  
  ```
  {"status":"You do not have access to this API.","status_code":"AP010002"}
  ```
- Because of this restriction, the Windows weather feed cannot be reused directly.

### OpenWeather Integration
- `screen.py` now supports **OpenWeather** as the weather source.  
- You must provide an OpenWeather API key (free accounts available).  
- Location can be specified as a city name or ZIP code.  
- The script queries OpenWeather every 10 minutes (default, adjustable).  
- On success, the following fields are extracted:
  - **Weather code → panel icon number (1–40)**  
  - **Daily low/high temperature**  
  - **Condition description** (used in dashboard only)  
  - **Zone (city, country)** (used in dashboard only)  

### Panel Behavior
- **Weather**: numeric code shows the corresponding icon (1=first icon, 40=last).  
- **TemprLo / TemprHi**: numeric values are displayed.  
- **Zone / Desc**: accepted in payload but never displayed on screen. They are “dead fields.”  

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

| OpenWeather condition | Example values                | Panel code |
|------------------------|--------------------------------|------------|
| Clear sky              | `clear sky`                   | 1          |
| Few clouds             | `few clouds`                  | 2          |
| Scattered clouds       | `scattered clouds`            | 3          |
| Broken clouds          | `broken clouds`               | 4          |
| Overcast clouds        | `overcast clouds`             | 5          |
| Light rain             | `light rain`                  | 6          |
| Moderate rain          | `moderate rain`               | 7          |
| Heavy rain             | `heavy intensity rain`        | 8          |
| Thunderstorm           | `thunderstorm`, `thunder`     | 9          |
| Light snow             | `light snow`                  | 10         |
| Snow                   | `snow`                        | 11         |
| Heavy snow             | `heavy snow`                  | 12         |
| Sleet                  | `sleet`                       | 13         |
| Mist / Fog / Haze      | `mist`, `fog`, `haze`         | 14         |
| Smoke / Dust / Sand    | `smoke`, `dust`, `sand`       | 15         |
| Tornado                | `tornado`                     | 16         |
| Drizzle                | `light intensity drizzle`     | 17         |
| Shower rain            | `shower rain`                 | 18         |
| Freezing rain          | `freezing rain`               | 19         |
| Extreme (hail, etc.)   | `hail`, `extreme`             | 20         |
| …                      | (extend mapping as needed)    | 21–40      |

(Only codes 1–40 are valid; unmapped conditions can be assigned arbitrarily within this range.)

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

Net iface      : wlp89s0f0
Net RX,TX      : 1.2 K/s, 3.0 K/s
Fan speed      : 1500 r/min
Volume         : 44 %
Battery        : 177 %

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

1. Copy `screen.py` into `/home/<YOUR_USER>/screen/screen.py`.  

2. Create a service file:

```ini
# /etc/systemd/system/atomman.service
[Unit]
Description=AtomMan Screen Daemon
After=network.target

[Service]
ExecStart=/usr/bin/python3 /home/<YOUR_USER>/screen/screen.py --start-delay 5
WorkingDirectory=/home/<YOUR_USER>/screen
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
| MEM  | 0x49| '4' | `{Memory:Samsung;Used:9.5;Available:22.3;Total:31.8;Useage:29}`                  |
| DISK | 0x4F| '5' | `{DiskName:Samsung SSD 980 PRO;Tempr:33;UsageSpace:222;AllSpace:931;Usage:24}`  |
| DATE | 0x6B| '6' | `{Date:2025/09/15;Time:14:22:10;Week:1;Weather:;TemprLo:,TemprHi:,Zone:,Desc:}` |
| NET  | 0x27| '7' | `{SPEED:1570;NETWORK:2.4M/s,312K/s}`                                            |
| VOL  | 0x10| '9' | `{VOLUME:45}`                                                                   |
| BAT  | 0x1A| '2' | `{Battery:97}`                                                                  |

---

## Troubleshooting

- **Fan shows `-1`** → Increase `--start-delay` so NVIDIA/hwmon drivers initialize.  
- **No serial access** → Add your user to `dialout` (`sudo usermod -aG dialout <YOUR_USER>`).  
- **Panel does not unlock** → Increase `--window` or `--attempts`.  
- **Network RX/TX stuck** → Ensure interface is up (`ip link show`).  
- **Weather blank** → Missing API key or internet access.  
- **Zone/Desc not showing** → Expected, panel ignores them.  

---

## License

MIT License — use freely for personal or commercial projects.
