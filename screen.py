#!/usr/bin/env python3
# AtomMan unlock + retries + (optional) colored dashboard + multi-vendor GPU + smart NIC picker
# ENQ (dev→host):   AA 05 <SEQ_ASCII> CC 33 C3 3C
# REPLY (host→dev): AA <TileID> 00 <SEQ_ASCII> {ASCII payload} CC 33 C3 3C
#
# Per-tile SEQs:
#   CPU  (0x53) → '2'
#   GPU  (0x36) → '3'
#   MEM  (0x49) → '4'
#   DISK (0x4F) → '5'
#   DATE (0x6B) → '6'
#   NET  (0x27) → '7'
#   VOL  (0x10) → '9'
#   BAT  (0x1A) → '2' (fallback)
#
# DATE tile payload note:
#   The screen accepts a “full” payload:
#     {Date:YYYY/MM/DD;Time:HH:MM:SS;Week:N;Weather:X;TemprLo:L,TemprHi:H,Zone:Z,Desc:D}
#   This program sends the full form but intentionally leaves Weather/TemprLo/TemprHi/Zone/Desc blank:
#     {Date:...;Time:...;Week:N;Weather:;TemprLo:,TemprHi:,Zone:,Desc:}
#   • Week:N is 0..6 for Sunday..Saturday  (Sunday=0).
#   • Weather is a NUMERIC code selecting a baked-in icon on the panel:
#       1..40  (1 = first icon, 40 = last icon). Leave blank to show none.
#
# FAN speed logic (for NET tile's SPEED = r/min):
#   1) Try Linux HWMON: first non-zero /sys/class/hwmon/hwmon*/fan*_input → RPM.
#   2) Else try NVIDIA: `nvidia-smi --query-gpu=fan.speed --format=csv,noheader,nounits` → percentage.
#      Convert % → RPM using a configurable maximum:
#        RPM = round( (percent / 100.0) * FAN_MAX_RPM )
#      Default FAN_MAX_RPM = 5000 (override with --fan-max-rpm or ATOMMAN_FAN_MAX_RPM).
#   3) If no source found, return -1 (explicit “unknown” for the panel).
#
# Network rates (NET tile's NETWORK field):
#   Auto-scales units based on magnitude:
#     < 1024 KB/s  → "X.X K/s"
#     < 1024 MB/s  → "X.X M/s"
#     otherwise    → "X.X G/s"
#
# Service tip:
#   If the fan shows -1 at boot, increase --start-delay to give drivers time to initialize.

import os, sys, time, subprocess, re, glob, argparse
import serial

# -------- Config (env overrides) --------
PORT    = os.getenv("ATOMMAN_PORT", "/dev/serial/by-id/usb-Synwit_USB_Virtual_COM-if00")
BAUD    = int(os.getenv("ATOMMAN_BAUD", "115120").replace("115120","115200"))  # guard typo → 115200
RTSCTS  = os.getenv("ATOMMAN_RTSCTS", "false").lower() in ("1","true","yes","on")
DSRDTR  = os.getenv("ATOMMAN_DSRDTR", "true").lower()  in ("1","true","yes","on")
TRAILER = b"\xCC\x33\xC3\x3C"

DEFAULT_WAIT_START = float(os.getenv("ATOMMAN_WAIT_START", "3.0"))
UNLOCK_WINDOW    = float(os.getenv("ATOMMAN_UNLOCK_SECONDS", "5.0"))
POST_WRITE_SLEEP = float(os.getenv("ATOMMAN_WRITE_SLEEP", "0.006"))

# Fan controls default via env, can be overridden by CLI
ENV_FAN_PREFER   = os.getenv("ATOMMAN_FAN_PREFER", "auto").lower()   # auto|hwmon|nvidia
ENV_FAN_MAX_RPM  = int(os.getenv("ATOMMAN_FAN_MAX_RPM", "5000"))

# -------- ANSI colors (dashboard only) --------
class C:
    R="\033[31m"; G="\033[32m"; Y="\033[33m"; B="\033[34m"; M="\033[35m"; C_="\033[36m"; W="\033[37m"
    BR="\033[91m"; BG="\033[92m"; BY="\033[93m"; BB="\033[94m"; BM="\033[95m"; BC="\033[96m"; BW="\033[97m"
    DIM="\033[2m"; RESET="\033[0m"
NOCOLOR = False
def colorize(txt, color):
    if NOCOLOR: return txt
    return f"{color}{txt}{C.RESET}"
def temp_color(t):
    try: t = float(t)
    except: return lambda s: s
    if t < 60:  return lambda s: colorize(s, C.BG)
    if t < 80:  return lambda s: colorize(s, C.BY)
    return            lambda s: colorize(s, C.BR)
def util_color(pct):
    try: pct=float(pct)
    except: return lambda s: s
    if pct < 40:  return lambda s: colorize(s, C.BG)
    if pct < 80:  return lambda s: colorize(s, C.BY)
    return               lambda s: colorize(s, C.BR)
def usage_color(pct):  # disk/mem usage
    try: pct=float(pct)
    except: return lambda s: s
    if pct < 70:  return lambda s: colorize(s, C.BG)
    if pct < 90:  return lambda s: colorize(s, C.BY)
    return               lambda s: colorize(s, C.BR)

# -------- Utilities --------
def _run(cmd, timeout=0.7):
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=timeout)
    except Exception:
        return ""
def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""

# -------- CPU --------
def cpu_model() -> str:
    for ln in _read("/proc/cpuinfo").splitlines():
        if ln.startswith("model name"): return ln.split(":",1)[1].strip()
    return "Linux CPU"
def cpu_usage_pct() -> int:
    def snap():
        parts=_read("/proc/stat").splitlines()[0].split()[1:]
        n=list(map(int,parts)); idle=n[3]+n[4]; total=sum(n)
        return idle,total
    i1,t1=snap(); time.sleep(0.08); i2,t2=snap()
    di,dt=i2-i1, t2-t1
    return max(0,min(100,int(round(100*(1-(di/float(dt or 1)))))))
def cpu_freq_khz() -> int:
    """
    Return *kHz* as required by the panel.
    sysfs provides kHz already; lscpu fallback (MHz) converted → kHz.
    """
    for p in ("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq",
              "/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_cur_freq"):
        s=_read(p).strip()
        if s.isdigit(): return max(0, int(s))  # already kHz
    out=_run(["lscpu"])
    m=re.search(r"CPU MHz:\s*([\d.]+)",out)
    return int(float(m.group(1))*1000) if m else 0
def cpu_temp_c() -> int:
    for hw in glob.glob("/sys/class/hwmon/hwmon*"):
        for n in range(8):
            p=f"{hw}/temp{n}_input"
            if os.path.exists(p):
                try:
                    v=int(open(p).read().strip()); return v//1000 if v>1000 else v
                except Exception: pass
    return 0

# -------- FAN (RPM) --------
def _fan_rpm_from_hwmon() -> int | None:
    """
    Scan hwmon for real RPM. Return first nonzero RPM found, or the maximum
    nonzero if multiple present. None if no fan files exist.
    """
    best = None
    for hm in glob.glob("/sys/class/hwmon/hwmon*"):
        for fan in glob.glob(os.path.join(hm, "fan*_input")):
            try:
                v = int(open(fan).read().strip())
                if v > 0:
                    best = v if best is None else max(best, v)
            except Exception:
                pass
    return best

def _fan_rpm_from_nvidia(max_rpm: int) -> int | None:
    """
    Use NVIDIA fan duty (%) and map to RPM via max_rpm.
    Returns None if nvidia-smi not present or no value.
    """
    out = _run(["nvidia-smi","--query-gpu=fan.speed","--format=csv,noheader,nounits"])
    if not out:
        return None
    try:
        # First GPU line
        line = out.splitlines()[0].strip()
        if not line:
            return None
        percent = float(line)
        # Note: 0% is a valid 0 RPM reading
        rpm = int(round((percent/100.0)*max(1, int(max_rpm))))
        return rpm
    except Exception:
        return None

def fan_rpm(prefer: str, max_rpm: int) -> int:
    """
    prefer: 'auto' | 'hwmon' | 'nvidia'
    Returns RPM if found (>=0). If no source available → -1.
    """
    prefer = (prefer or "auto").lower()
    if prefer == "hwmon":
        v = _fan_rpm_from_hwmon()
        if v is not None: return v
        v = _fan_rpm_from_nvidia(max_rpm)
        return v if v is not None else -1
    if prefer == "nvidia":
        v = _fan_rpm_from_nvidia(max_rpm)
        if v is not None: return v
        v = _fan_rpm_from_hwmon()
        return v if v is not None else -1
    # auto
    v = _fan_rpm_from_hwmon()
    if v is not None: return v
    v = _fan_rpm_from_nvidia(max_rpm)
    return v if v is not None else -1

# -------- GPU (NVIDIA/AMD/Intel/fallback) --------
def clean_gpu_name(name: str) -> str:
    s = name.strip()
    s = re.sub(r"\(R\)|\(TM\)|NVIDIA Corporation|Advanced Micro Devices,? Inc\.?|Intel\(R\)\s*", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    return s or "GPU"
def gpu_info():
    # NVIDIA
    out = _run(["nvidia-smi","--query-gpu=name,temperature.gpu,utilization.gpu","--format=csv,noheader,nounits"])
    if out:
        try:
            name,temp,util=[x.strip() for x in out.splitlines()[0].split(",")]
            return clean_gpu_name(name), int(temp), int(util)
        except Exception:
            pass
    # AMD ROCm
    out = _run(["rocm-smi","--showtemp","--showuse"])
    if out:
        tm = re.search(r"(\d+(\.\d+)?)\s*c", out, re.I)
        um = re.search(r"(\d+)\s*%", out)
        temp = int(float(tm.group(1))) if tm else 0
        util = int(um.group(1)) if um else 0
        nm = re.search(r"GPU\[\d+\].*?\s(.*?)\s{2,}", out)
        name = nm.group(1).strip() if nm else "AMD Radeon"
        return clean_gpu_name(name), temp, util
    # Intel iGPU (best-effort)
    name = ""
    for path in ("/sys/class/drm/card0/device/product_name",
                 "/sys/class/drm/card0/device/name"):
        if os.path.exists(path):
            name = _read(path).strip(); break
    if not name:
        pci = _run(["lspci","-mmnn"])
        m = re.search(r'VGA compatible controller \[0300\]\s+"([^"]+)"', pci)
        if m: name = m.group(1)
    temp = 0
    for cand in glob.glob("/sys/class/drm/card0/device/hwmon/hwmon*/temp*_input"):
        try: temp = int(open(cand).read().strip())//1000; break
        except Exception: pass
    if name:
        return clean_gpu_name(name), temp, 0
    return "GPU", 0, 0

# -------- Memory / Disk --------
def mem_info():
    d={}
    for ln in _read("/proc/meminfo").splitlines():
        parts=ln.replace(":","").split()
        if len(parts)>=2 and parts[1].isdigit(): d[parts[0]]=int(parts[1])  # kB
    total=d.get("MemTotal",0); avail=d.get("MemAvailable",0); used=max(0,total-avail)
    to_gb=lambda kb: round(kb/1024.0/1024.0,1)
    usage=int(round(100.0*(used/float(total or 1))))
    return (to_gb(used),to_gb(avail),to_gb(total),usage)
def disk_numbers():
    st=os.statvfs("/")
    tot_b=st.f_frsize*st.f_blocks; avail_b=st.f_frsize*st.f_bavail; used_b=tot_b-avail_b
    to_gb=lambda b: int(round(b/1024/1024/1024))
    usage=int(round(100.0*(used_b/float(tot_b or 1))))
    return (to_gb(used_b), to_gb(tot_b), usage)

# ---- RAM & Disk vendor (cached) ----
_cache={"ram":("",0.0),"disk":("",0.0)}
def _cache_get(k,ttl=3600):
    v,t=_cache.get(k,("",0.0)); return v if v and time.time()-t<ttl else None
def _cache_set(k,v): _cache[k]=(v,time.time())
def ram_label():
    cached=_cache_get("ram")
    if cached is not None: return cached
    manu=""
    out = _run(["dmidecode","-t","memory"]) or _run(["sudo","-n","dmidecode","-t","memory"])
    if out:
        m=re.search(r"^\s*Manufacturer:\s*(.+)$",out,re.MULTILINE|re.IGNORECASE)
        if m:
            manu=m.group(1).strip()
            if manu in ("Undefined","Not Specified","Unknown","To Be Filled By O.E.M."): manu=""
    if not manu:
        out=_run(["lshw","-class","memory"])
        if out:
            m=re.search(r"^\s*manufacturer:\s*(.+)$",out,re.MULTILINE|re.IGNORECASE)
            if m: manu=m.group(1).strip()
    manu=(manu.replace("Micron Technology","Micron")
               .replace("Samsung Electronics","Samsung")
               .replace("HYNIX","SK hynix")
               .replace("Hynix","SK hynix")).strip()
    _cache_set("ram",manu); return manu
def disk_label():
    cached=_cache_get("disk")
    if cached is not None: return cached
    label=""
    try:
        for n in sorted(glob.glob("/sys/class/nvme/nvme*")):
            model=_read(os.path.join(n,"model")).strip()
            if model: label=model; break
    except Exception: pass
    if not label:
        try:
            out=_run(["lsblk","-dno","NAME,MODEL,VENDOR"])
            root_dev=""
            try:
                src=_run(["findmnt","-nro","SOURCE","/"]).strip()
                root_dev=os.path.basename(re.sub(r"p?\d+$","",src.replace("/dev/","")))
            except Exception: pass
            pick=None
            for ln in out.splitlines():
                parts=ln.split(None,2)
                if not parts: continue
                name=parts[0]; rest=parts[1:] if len(parts)>1 else []
                if root_dev and name==root_dev: pick=rest; break
                if not root_dev and pick is None: pick=rest
            if pick: label=" ".join(pick).strip()
        except Exception: pass
    label=re.sub(r"\s+"," ",label).strip()
    _cache_set("disk",label); return label

# ---------- Network (active iface picker, prefer LAN) ----------
def _sh(cmd, timeout=0.6):
    try: return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=timeout).strip()
    except Exception: return ""
def _is_wireless(iface: str) -> bool:
    return os.path.isdir(f"/sys/class/net/{iface}/wireless")
def _iface_info(iface: str) -> dict:
    info = {"name": iface, "up": False, "carrier": False, "wireless": _is_wireless(iface)}
    try:
        with open(f"/sys/class/net/{iface}/operstate") as f:
            info["up"] = (f.read().strip() == "up")
    except Exception:
        pass
    try:
        with open(f"/sys/class/net/{iface}/carrier") as f:
            info["carrier"] = (f.read().strip() == "1")
    except Exception:
        pass
    return info
def _default_route_ifaces() -> list:
    out = _sh(["ip", "-o", "route", "show", "default"])
    devs = []
    for line in out.splitlines():
        m = re.search(r"\bdev\s+([^\s]+)", line)
        if m: devs.append(m.group(1))
    return list(dict.fromkeys(devs))
def _list_candidate_ifaces() -> list:
    try:
        return [i for i in sorted(os.listdir("/sys/class/net")) if i != "lo"]
    except Exception:
        return []
def _pick_iface(preferred: str | None = None) -> str | None:
    if preferred:  # env override
        return preferred
    defaults = _default_route_ifaces()
    ranked = []
    for i in defaults:
        inf = _iface_info(i)
        score = (2 if (inf["up"] and inf["carrier"]) else 1 if inf["up"] else 0) + (1 if not inf["wireless"] else 0)
        ranked.append((score, not inf["wireless"], inf["name"]))
    ranked.sort(reverse=True)
    for score, _wired, name in ranked:
        if score > 0: return name
    cands=[]
    for i in _list_candidate_ifaces():
        inf = _iface_info(i)
        score = (2 if (inf["up"] and inf["carrier"]) else 1 if inf["up"] else 0) + (1 if not inf["wireless"] else 0)
        cands.append((score, not inf["wireless"], inf["name"]))
    cands.sort(reverse=True)
    for score, _wired, name in cands:
        if score > 0: return name
    pool=_list_candidate_ifaces()
    return pool[0] if pool else None
def _read_netdev():
    try:
        with open("/proc/net/dev","r") as f:
            return f.read().splitlines()
    except Exception:
        return []
def _parse_netdev(lines, iface):
    for ln in lines:
        if ":" not in ln: continue
        name, rest = ln.split(":", 1)
        if name.strip() == iface:
            cols = rest.split()
            if len(cols) >= 16:
                rx = int(cols[0]); tx = int(cols[8])
                return rx, tx
    return None, None
class NetMeter:
    def __init__(self):
        env = os.getenv("ATOMMAN_NET_IFACE", "").strip() or None
        self.iface = _pick_iface(env)
        self.rx0 = self.tx0 = None
        self.t0 = None
        self._prime()
    def _prime(self):
        if not self.iface: return
        lines = _read_netdev()
        rx, tx = _parse_netdev(lines, self.iface)
        if rx is None:
            self.iface = _pick_iface()
            if not self.iface: return
            lines = _read_netdev()
            rx, tx = _parse_netdev(lines, self.iface)
        if rx is not None:
            self.rx0, self.tx0, self.t0 = rx, tx, time.time()
    def maybe_repick(self):
        if not self.iface:
            self.iface = _pick_iface(); self._prime(); return
        inf = _iface_info(self.iface)
        if not inf["up"] or (inf["wireless"] and not inf["carrier"]):
            new = _pick_iface()
            if new and new != self.iface:
                self.iface = new
                self._prime()
    def rates_ks(self):
        """Return RX,TX in *KB/s* as floats (kiloBYTES per second)."""
        self.maybe_repick()
        if not self.iface:
            return None, None
        lines = _read_netdev()
        rx1, tx1 = _parse_netdev(lines, self.iface)
        if rx1 is None or self.rx0 is None:
            self._prime(); return None, None
        t1 = time.time(); dt = max(1e-3, t1 - self.t0)
        rxk = (rx1 - self.rx0) / dt / 1024.0
        txk = (tx1 - self.tx0) / dt / 1024.0
        self.rx0, self.tx0, self.t0 = rx1, tx1, t1
        rxk = max(0.0, rxk); txk = max(0.0, txk)
        return rxk, txk
_nm = NetMeter()

# -------- Helpers --------
def _fmt_rate(rate_kbs: float) -> str:
    """Format RX/TX rate with auto unit scaling (K/M/G per second)."""
    if rate_kbs is None:
        return "N/A"
    if rate_kbs < 1024.0:
        return f"{rate_kbs:.1f} K/s"
    mbps = rate_kbs / 1024.0
    if mbps < 1024.0:
        return f"{mbps:.1f} M/s"
    gbps = mbps / 1024.0
    return f"{gbps:.1f} G/s"

# -------- Tile payload generators --------
def p_cpu():
    t0=cpu_temp_c()
    # NOTE: Freq must be kHz for the panel
    return f"{{CPU:{cpu_model()};Tempr:{t0};Useage:{cpu_usage_pct()};Freq:{cpu_freq_khz()};Tempr1:{t0};}}"

def p_gpu():
    name,temp,util=gpu_info()
    # GPU tile (no trailing ';' before '}')
    return f"{{GPU:{name};Tempr:{temp};Useage:{util}}}"

def p_mem():
    used,avail,total,usage=mem_info()
    manu=ram_label(); label=f"Memory ({manu})" if manu else "Memory"
    return f"{{Memory:{label};Used:{used};Available:{avail};Total:{total};Useage:{usage}}}"

def p_dsk():
    used,total,usage=disk_numbers()
    lab=disk_label() or "Disk"
    return f"{{DiskName:{lab};Tempr:33;UsageSpace:{used};AllSpace:{total};Usage:{usage}}}"

def p_date():
    # Full payload form after Week:N, but leave the extra fields blank.
    t=time.localtime()
    # Week must be 0..6 with Sunday=0 (Python's tm_wday is Mon=0..Sun=6)
    week_num = (t.tm_wday + 1) % 7
    return (
        f"{{Date:{t.tm_year:04d}/{t.tm_mon:02d}/{t.tm_mday:02d};"
        f"Time:{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d};"
        f"Week:{week_num};Weather:;TemprLo:,TemprHi:,Zone:,Desc:}}"
    )

def p_net(fan_prefer: str, fan_max_rpm: int):
    rxk, txk = _nm.rates_ks()
    rpm = fan_rpm(fan_prefer, fan_max_rpm)
    if rxk is None or txk is None:
        return f"{{SPEED:{rpm};NETWORK:N/A,N/A}}"
    # SPEED is r/min (RPM); NETWORK shows auto-scaled text for each direction
    return f"{{SPEED:{rpm};NETWORK:{_fmt_rate(rxk)},{_fmt_rate(txk)}}}"

def p_vol():
    out=_run(["pactl","get-sink-volume","@DEFAULT_SINK@"], timeout=0.7)
    m=re.search(r"(\d+)%",out); vol=int(m.group(1)) if m else -1
    return f"{{VOLUME:{vol}}}"

def p_bat():
    try:
        for base in os.listdir("/sys/class/power_supply"):
            if base.startswith("BAT"):
                with open(f"/sys/class/power_supply/{base}/capacity") as f:
                    return f"{{Battery:{int(f.read().strip())}}}"
    except Exception: pass
    return "{Battery:177}"

# Tile IDs & rotations
CPU, GPU, MEM, DSK, DAT, NET, VOL, BAT = 0x53,0x36,0x49,0x4F,0x6B,0x27,0x10,0x1A
UNLOCK_ROT = [(CPU,p_cpu),(GPU,p_gpu),(MEM,p_mem)]  # reliable unlock

# -------- Per-tile SEQ mapping (CPU='2') --------
SEQ_FOR = {CPU:'2', GPU:'3', MEM:'4', DSK:'5', DAT:'6', NET:'7', VOL:'9', BAT:'2'}
def seq_for(tile_id: int) -> int:
    ch = SEQ_FOR.get(tile_id, '2')
    return (ord('<') if ch == '<' else ord(ch))

# -------- Protocol --------
def read_enq(ser):
    if ser.read(1)!=b"\xAA": return None
    if ser.read(1)!=b"\x05": return None
    b3=ser.read(1)
    if not b3: return None
    if ser.read(4)!=TRAILER: return None
    return b3[0]  # ASCII during BOOT; tile_id during NORMAL (panel quirk)
def build_reply(id_byte:int, seq_ascii:int, txt:str)->bytes:
    return bytes([0xAA,id_byte,0x00,seq_ascii]) + txt.encode("latin-1","ignore") + TRAILER
def open_serial(wait_start: float):
    time.sleep(wait_start)  # allow USB CDC / drivers / fans to come up
    s=serial.Serial(PORT,BAUD,timeout=1.0,write_timeout=1.0,dsrdtr=DSRDTR,rtscts=RTSCTS)
    try:
        s.reset_input_buffer(); s.reset_output_buffer()
    except Exception: pass
    return s

# -------- Dashboard (optional) --------
def render_dashboard(latest):
    sys.stdout.write("\033[2J\033[H")  # clear + home
    t=time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"{colorize('AtomMan — Active', C.BW)}   Time: {colorize(t, C.BC)}")
    print("-"*72)
    tc = temp_color(latest.get('cpu_temp','?'))
    uc = util_color(latest.get('cpu_usage','?'))
    print(f"Processor type : {latest.get('cpu_model','')}")
    print(f"Processor temp : {tc(str(latest.get('cpu_temp','?')) + ' °C')}")
    print(f"CPU usage      : {uc(str(latest.get('cpu_usage','?')) + ' %')}")
    print(f"CPU freq       : {str(latest.get('cpu_freq_khz','?'))} kHz")
    print()
    gname = latest.get('gpu_name','N/A')
    gtc = temp_color(latest.get('gpu_temp','0'))
    guc = util_color(latest.get('gpu_util','0'))
    print(f"GPU model      : {gname}")
    print(f"GPU temp       : {gtc(str(latest.get('gpu_temp','0')) + ' °C')}")
    print(f"GPU usage      : {guc(str(latest.get('gpu_util','0')) + ' %')}")
    print()
    muc = usage_color(latest.get('mem_usage','?'))
    print(f"RAM (vendor)   : {latest.get('ram_vendor','')}")
    print(f"RAM used       : {str(latest.get('mem_used','?'))} GB")
    print(f"RAM avail      : {str(latest.get('mem_avail','?'))} GB")
    print(f"RAM total      : {str(latest.get('mem_total','?'))} GB")
    print(f"RAM usage      : {muc(str(latest.get('mem_usage','?')) + ' %')}")
    print()
    duc = usage_color(latest.get('disk_usage','?'))
    print(f"Disk (label)   : {latest.get('disk_label','')}")
    print(f"Disk used      : {str(latest.get('disk_used','?'))} GB")
    print(f"Disk total     : {str(latest.get('disk_total','?'))} GB")
    print(f"Disk usage     : {duc(str(latest.get('disk_usage','?')) + ' %')}")
    print()
    iface = latest.get('iface', 'N/A')
    print(f"Net iface      : {iface}")
    rx = latest.get('net_rx', None)
    tx = latest.get('net_tx', None)
    print(f"Net RX,TX      : {_fmt_rate(rx)}, {_fmt_rate(tx)}")
    print(f"Fan speed      : {str(latest.get('fan_rpm','-1'))} r/min")
    print(f"Volume         : {str(latest.get('volume','-1'))} %")
    print(f"Battery        : {str(latest.get('battery','177'))} %")
    print("-"*72)
    sys.stdout.flush()

def update_latest_from_payload(id_byte, latest, fan_prefer, fan_max_rpm):
    if id_byte==CPU:
        latest.update({
            "cpu_model": cpu_model(),
            "cpu_temp" : cpu_temp_c(),
            "cpu_usage": cpu_usage_pct(),
            "cpu_freq_khz" : cpu_freq_khz(),
        })
    elif id_byte==GPU:
        n,t,u=gpu_info()
        latest.update({"gpu_name": n, "gpu_temp": t, "gpu_util": u})
    elif id_byte==MEM:
        used,avail,total,usage=mem_info()
        latest.update({
            "ram_vendor": ram_label() or "",
            "mem_used": used, "mem_avail": avail, "mem_total": total, "mem_usage": usage
        })
    elif id_byte==DSK:
        used,total,usage=disk_numbers()
        latest.update({
            "disk_label": disk_label() or "Disk",
            "disk_used": used, "disk_total": total, "disk_usage": usage
        })
    elif id_byte==NET:
        rxk,txk=_nm.rates_ks()
        latest.update({
            "net_rx": rxk,                     # store RAW float (KB/s)
            "net_tx": txk,
            "fan_rpm": fan_rpm(fan_prefer, fan_max_rpm),
            "iface": _nm.iface or "N/A"
        })
    elif id_byte==VOL:
        out=_run(["pactl","get-sink-volume","@DEFAULT_SINK@"], timeout=0.7)
        m=re.search(r"(\d+)%",out); vol=int(m.group(1)) if m else -1
        latest.update({"volume": vol})
    elif id_byte==BAT:
        pct=None
        try:
            for base in os.listdir("/sys/class/power_supply"):
                if base.startswith("BAT"):
                    with open(f"/sys/class/power_supply/{base}/capacity") as f:
                        pct=int(f.read().strip()); break
        except Exception: pass
        latest.update({"battery": pct if pct is not None else 177})

# -------- Activation + Retry + Main loop --------
def is_ascii_seq(b): return (0x30<=b<=0x39) or (b==0x3C)

def unlock_attempt(ser, attempt_idx, latest, unlock_window, fan_prefer, fan_max_rpm, dashboard):
    print(f"[Attempt {attempt_idx}/3] Unlock window {unlock_window:.0f}s — echoing SEQ with CPU→GPU→MEM")
    start=time.time(); idx=0; boot_replies=0; enq_times=[]; activated=False
    while time.time()-start < unlock_window:
        seq=read_enq(ser)
        if seq is None:
            if dashboard:
                render_dashboard(latest)
            continue
        enq_times.append(time.time())
        enq_times=[t for t in enq_times if time.time()-t <= 2.0]
        tile, maker = UNLOCK_ROT[idx % len(UNLOCK_ROT)]
        payload = maker()
        frm = build_reply(tile, seq, payload)  # echo seq during unlock
        ser.write(frm); ser.flush(); time.sleep(POST_WRITE_SLEEP)
        update_latest_from_payload(tile, latest, fan_prefer, fan_max_rpm)
        idx += 1
        if is_ascii_seq(seq): boot_replies += 1
        if (boot_replies >= 3) and (len(enq_times) >= 5):
            activated=True
            print(f"[Attempt {attempt_idx}] Activated (ENQs flowing).")
            break
    if not activated:
        print(f"[Attempt {attempt_idx}] No activation within window.")
    return activated

def main():
    global NOCOLOR
    ap=argparse.ArgumentParser(
        description="AtomMan daemon (tiles + optional dashboard)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--attempts", type=int, default=3, help="Total unlock attempts")
    ap.add_argument("--window", type=float, default=UNLOCK_WINDOW, help="Seconds per attempt during unlock")
    ap.add_argument("--no-color", action="store_true", help="Disable ANSI colors in dashboard")
    ap.add_argument("--dashboard", action="store_true", help="Show live dashboard in console (off by default)")
    ap.add_argument("--start-delay", type=float, default=DEFAULT_WAIT_START,
                    help="Seconds to sleep before opening serial (helps driver init)")
    ap.add_argument("--fan-prefer", choices=["auto","hwmon","nvidia"], default=ENV_FAN_PREFER,
                    help="Preferred fan source (auto tries hwmon then NVIDIA)")
    ap.add_argument("--fan-max-rpm", type=int, default=ENV_FAN_MAX_RPM,
                    help="Used only when NVIDIA reports percent; RPM = % * this / 100")
    args=ap.parse_args()
    NOCOLOR = args.no_color

    # Open serial (with start delay to let drivers/hw settle)
    ser = open_serial(args.start_delay)
    print(f"[AtomMan] on {PORT} @ {BAUD} (RTSCTS={RTSCTS} DSRDTR={DSRDTR}; start_delay={args.start_delay:.1f}s; fan={args.fan_prefer}, fan_max_rpm={args.fan_max_rpm})")

    latest = {"cpu_model": cpu_model()}

    # Activation attempts
    activated=False
    for i in range(1, args.attempts+1):
        activated = unlock_attempt(ser, i, latest, args.window, args.fan_prefer, args.fan_max_rpm, args.dashboard)
        if activated:
            break
        try:
            ser.setDTR(False); time.sleep(0.05); ser.setDTR(True)
        except Exception:
            pass
        time.sleep(0.3)

    if not activated:
        print("[WARN] Screen might not be fully activated; continuing anyway.")
    else:
        print("[OK] Screen activated — switching to steady-state.")

    # Steady state: rotate tiles; use fixed per-tile SEQs
    FULL_ROT = [
        (CPU,p_cpu),(GPU,p_gpu),(MEM,p_mem),(DSK,p_dsk),
        (DAT,p_date),(NET,lambda: p_net(args.fan_prefer, args.fan_max_rpm)),
        (VOL,p_vol),(BAT,p_bat)
    ]
    idx=0
    last_render=0.0
    while True:
        enq3=read_enq(ser)
        if enq3 is None:
            if args.dashboard and (time.time()-last_render>1.0):
                render_dashboard(latest)
                last_render=time.time()
            continue

        tile, maker = FULL_ROT[idx % len(FULL_ROT)]
        payload = maker()
        seq = seq_for(tile)
        frm = build_reply(tile, seq, payload)
        ser.write(frm); ser.flush(); time.sleep(POST_WRITE_SLEEP)

        update_latest_from_payload(tile, latest, args.fan_prefer, args.fan_max_rpm)

        if args.dashboard:
            now=time.time()
            if now - last_render >= 0.25:  # ~4 fps max
                render_dashboard(latest)
                last_render=now

        idx = (idx + 1) % 1_000_000

if __name__=="__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass