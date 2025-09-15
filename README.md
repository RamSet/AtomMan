# AtomMan Host Daemon

A Python daemon that communicates with the AtomMan display over a serial port and provides system telemetry tiles: CPU, GPU, Memory, Disk, Date/Time/Week, Network, Volume, and Battery. It also includes an optional on-console dashboard.

---

## Protocol

- **ENQ (dev→host)**: `AA 05 <SEQ_ASCII> CC 33 C3 3C`  
- **REPLY (host→dev)**: `AA <TileID> 00 <SEQ_ASCII> {ASCII payload} CC 33 C3 3C`

**Tile IDs / SEQ mapping:**

| Tile | ID   | SEQ |
|------|------|-----|
| CPU  | 0x53 | '2' |
| GPU  | 0x36 | '3' |
| MEM  | 0x49 | '4' |
| DISK | 0x4F | '5' |
| DATE | 0x6B | '6' |
| NET  | 0x27 | '7' |
| VOL  | 0x10 | '9' |
| BAT  | 0x1A | '2' (fallback) |

---

## Telemetry Provided

- **CPU**: model, temp (°C), usage (%), and **frequency in kHz** (panel requires kHz).
- **GPU**: name, temp (°C), utilization (%). Works with NVIDIA, AMD ROCm, and Intel iGPU (best effort).
- **Memory**: used, available, total (GB), usage (%). Shows vendor if detected.
- **Disk**: used, total (GB), usage (%). Shows NVMe model or lsblk vendor label.
- **Date/Time/Week**:
  - Payload form:
    ```
    {Date:YYYY/MM/DD;Time:HH:MM:SS;Week:N;Weather:;TemprLo:,TemprHi:,Zone:,Desc:}
    ```
  - **Week:N** is **0..6**, where **0 = Sunday** … **6 = Saturday**.
  - If `Weather` is set: numeric code **1..40** maps to built-in screen icons.
- **Network**: RX/TX throughput auto-scaled to `K/s`, `M/s`, or `G/s`.
- **Fan speed (r/min)**:
  1. **hwmon**: first non-zero `/sys/class/hwmon/hwmon*/fan*_input`.
  2. **NVIDIA**: `nvidia-smi --query-gpu=fan.speed --format=csv,noheader,nounits` → % mapped to RPM using `--fan-max-rpm` (default 5000).
  3. Fallback: `-1` (unknown).
- **Volume**: system default sink volume (%).
- **Battery**: capacity from `/sys/class/power_supply/BAT*`.

---

## Installation

# Clone
git clone https://github.com/ramset/atomman.git
cd atomman

# System deps (Debian/Ubuntu examples)
sudo apt-get update
sudo apt-get install -y python3 python3-serial 

# NVIDIA optional
# nvidia-smi comes with the NVIDIA driver; install from your distro or NVIDIA packages

---
## Permissions

Allow your user to access the AtomMan serial port:

```bash
sudo usermod -aG dialout <YOUR_USER>
```

(Replace `<YOUR_USER>` with your Linux username. Log out and back in after running.)

---

## Usage

Run manually:

```bash
python3 screen.py --dashboard
```

Key options:

- `--dashboard` : show live console dashboard (off by default).
- `--attempts N` : unlock retries (default 3).
- `--window SEC` : seconds per unlock attempt.
- `--start-delay SEC` : wait before serial open (default 3.0).
- `--fan-prefer {auto|hwmon|nvidia}` : pick fan source (default auto).
- `--fan-max-rpm N` : RPM to map NVIDIA % duty cycle (default 5000).
- `--no-color` : disable ANSI color in dashboard.

For all options:

```bash
python3 screen.py --help
```

---

## Service Setup

Create a systemd service file `/etc/systemd/system/atomman.service`:

```ini
[Unit]
Description=AtomMan Host Daemon
After=multi-user.target

[Service]
ExecStart=/usr/bin/python3 /home/<YOUR_USER>/atomman/screen.py --dashboard
WorkingDirectory=/home/<YOUR_USER>/atomman
Restart=always
User=<YOUR_USER>

[Install]
WantedBy=multi-user.target
```

Enable + start:

```bash
sudo systemctl enable atomman
sudo systemctl start atomman
```

---

## Notes

- If fan reports `-1`, increase `--start-delay` to give GPU/driver time to initialize.
- Date tile leaves weather fields blank by default; can be set to show icons.
- Network throughput is shown as human-scaled units (K/M/G per second).
