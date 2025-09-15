# `screen.py` – AtomMan Serial Display Daemon

`screen.py` is a Python daemon that drives the **AtomMan USB serial display panel**, unlocking it after boot and continuously updating all tiles with system metrics (CPU, GPU, memory, disk, date, network, volume, battery, and fan speed).  

The script is designed to run as a **systemd service** on Linux, with optional console dashboard output for debugging.

---

## ✨ Features

- **Unlock & Retry Logic** – Robust startup handshake with retries until the panel activates.  
- **Per-Tile Payloads** – Updates CPU, GPU, memory, disk, date, net, volume, battery.  
- **Fan Speed**  
  - Primary: Linux **hwmon** (`/sys/class/hwmon/*/fan*_input`)  
  - Fallback: NVIDIA (`nvidia-smi --query-gpu=fan.speed`) → converts % to RPM  
  - Final fallback: `-1` if no source found  
- **CPU Frequency in kHz** (panel requires kHz, not MHz)  
- **Date/Time/Week Tile**  
  - Week uses `0..6` where `0=Sunday … 6=Saturday`  
  - Full payload form includes placeholders for weather fields  
  - Weather accepts numeric codes `1..40` → panel icons  
- **Network Throughput Auto-Scaling**  
  - Values shown in `K/s`, `M/s`, or `G/s` depending on rate  
- **Optional Console Dashboard** (`--dashboard`) with ANSI colors  
- **Configurable Start Delay** (ensures drivers/fans are ready before panel comms start)  
- **Systemd Ready** – run as a background service with restart policy.  

---

## 📦 Requirements

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

## ⚡ Permissions

The AtomMan display is exposed as a USB serial device under `/dev/serial/by-id/...`.  
Grant your user access by adding them to the `dialout` group:

```bash
sudo usermod -aG dialout <YOUR_USER>
```

Log out and back in for this to take effect.

---

## 🚀 Usage

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

## 🖥 Example Dashboard

When `--dashboard` is enabled:

```
AtomMan — Active   Time: 2025-09-15 14:22:10
----------------------------------------------------------------
Processor type : AMD Ryzen 9 7940HS
Processor temp : 62 °C
CPU usage      : 27 %
CPU freq       : 3,900,000 kHz

GPU model      : NVIDIA RTX 4060
GPU temp       : 70 °C
GPU usage      : 43 %

RAM (vendor)   : Samsung
RAM used       : 9.5 GB
RAM avail      : 22.3 GB
RAM total      : 31.8 GB
RAM usage      : 29 %

Disk (label)   : Samsung SSD 980 PRO
Disk used      : 222 GB
Disk total     : 931 GB
Disk usage     : 24 %

Net iface      : eth0
Net RX,TX      : 2.4M/s, 312K/s
Fan speed      : 1570 r/min
Volume         : 45 %
Battery        : 97 %
----------------------------------------------------------------
```

---

## 🔌 Systemd Service Setup

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

## ⚙️ Internals

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

## 🛠 Troubleshooting

- **Fan shows `-1`** → Increase `--start-delay` so NVIDIA/hwmon drivers initialize.  
- **No serial access** → Add your user to `dialout` (`sudo usermod -aG dialout <YOUR_USER>`).  
- **Panel does not unlock** → Increase `--window` or `--attempts`.  
- **Network RX/TX stuck** → Ensure interface is up (`ip link show`).  

---

## 📜 License

MIT License — use freely for personal or commercial projects.
