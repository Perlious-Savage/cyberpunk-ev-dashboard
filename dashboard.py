"""
EV Charger Security Dashboard — Flask Backend
Manages 8 EVSE chargers, the physical EVSE-01 node (Arduino + Pi USB sentinel),
simulation engine, and REST API.
"""

from flask import Flask, render_template, jsonify, request
import glob
import hmac
import os
import threading
import time
import random
import webbrowser
from datetime import datetime

# Optional: pyserial for Arduino connection
try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

app = Flask(__name__)

# ── Charger Fleet ────────────────────────────────────────────────────────────

chargers = [
    {"id": "EVSE-01", "trust": 100, "state": "FULL_TRUST", "kw": 9.3,  "base_kw": 9.3,  "tamper": False, "usb": False, "isolated": False, "door_open": False},
    {"id": "EVSE-02", "trust": 100, "state": "FULL_TRUST", "kw": 11.3, "base_kw": 11.3, "tamper": False, "usb": False, "isolated": False, "door_open": False},
    {"id": "EVSE-03", "trust": 100, "state": "FULL_TRUST", "kw": 20.4, "base_kw": 20.4, "tamper": False, "usb": False, "isolated": False, "door_open": False},
    {"id": "EVSE-04", "trust": 100, "state": "FULL_TRUST", "kw": 17.2, "base_kw": 17.2, "tamper": False, "usb": False, "isolated": False, "door_open": False},
    {"id": "EVSE-05", "trust": 100, "state": "FULL_TRUST", "kw": 19.2, "base_kw": 19.2, "tamper": False, "usb": False, "isolated": False, "door_open": False},
    {"id": "EVSE-06", "trust": 100, "state": "FULL_TRUST", "kw": 17.2, "base_kw": 17.2, "tamper": False, "usb": False, "isolated": False, "door_open": False},
    {"id": "EVSE-07", "trust": 100, "state": "FULL_TRUST", "kw": 18.6, "base_kw": 18.6, "tamper": False, "usb": False, "isolated": False, "door_open": False},
    {"id": "EVSE-08", "trust": 100, "state": "FULL_TRUST", "kw": 12.0, "base_kw": 12.0, "tamper": False, "usb": False, "isolated": False, "door_open": False},
]

events = []
event_seq = 0
latest_distance = -1.0
serial_connected = False

# ── Physical Station (EVSE-01 ↔ Arduino + Pi USB port) ───────────────────────

STATION_ID = "EVSE-01"
DOOR_THRESHOLD_CM = 10.0   # calibration: reading > this = enclosure open
DOOR_DEBOUNCE = 3          # consecutive readings needed to flip door state (~300 ms)
CMD_RESEND_S = 2.0         # re-assert hardware state in case the Uno reset
MAINT_CODE = os.environ.get("MAINT_CODE", "7777")

STATE_NAMES = ["NOMINAL", "AUTHORIZED MAINTENANCE", "PHYSICAL TAMPER", "CRITICAL COMPROMISE"]
STATE_CMDS = ["CMD:ALARM_OFF", "CMD:ALARM_OFF", "CMD:LED_ON", "CMD:ALARM_ON"]

station = {
    "state": 0,
    "door_open": False,
    "usb_present": False,
    "usb_devices": [],
    "maintenance": False,
    "maint_since": None,
    "compromised": False,   # latched until USB removed AND operator restores
}
# ponytail: one global lock, fine for a single physical node
station_lock = threading.RLock()
ser = None
ser_lock = threading.Lock()
_door_streak = 0

# ── Helpers ───────────────────────────────────────────────────────────────────

def add_event(msg):
    global event_seq
    event_seq += 1
    ts = datetime.now().strftime("%H:%M:%S")
    events.append({"id": event_seq, "time": ts, "msg": msg})
    if len(events) > 100:
        events.pop(0)

def find_charger(cid):
    for c in chargers:
        if c["id"] == cid:
            return c
    return None

# ── Attack / Restore Logic ───────────────────────────────────────────────────

def apply_tamper(cid, from_sensor=False):
    c = find_charger(cid)
    if not c or c["isolated"]:
        return
    c["tamper"] = True
    c["door_open"] = True
    c["trust"] = 60
    c["state"] = "THROTTLED"
    c["kw"] = round(c["base_kw"] * 0.6, 1)
    source = "sensor" if from_sensor else "simulation"
    add_event(f"[WARN] {cid} enclosure tamper detected ({source}) — trust reduced to 60")

def apply_usb(cid):
    c = find_charger(cid)
    if not c:
        return
    c["usb"] = True
    c["trust"] = 0
    c["state"] = "ISOLATED"
    c["kw"] = 0.0
    c["isolated"] = True
    add_event(f"[CRIT] {cid} unauthorized USB device detected — isolating from grid")
    threading.Timer(3.0, finalize_isolation, args=[cid]).start()

def finalize_isolation(cid):
    c = find_charger(cid)
    if c and c["usb"]:
        add_event(f"[CRIT] {cid} isolated from grid")

def restore_charger(cid):
    c = find_charger(cid)
    if not c:
        return
    c["tamper"] = False
    c["usb"] = False
    c["isolated"] = False
    c["door_open"] = False
    c["trust"] = 100
    c["state"] = "FULL_TRUST"
    c["kw"] = c["base_kw"]
    add_event(f"[OK] {cid} restored — back online at full trust")

# ── Station State Machine ────────────────────────────────────────────────────

def station_state(door_open, usb_present, maintenance, compromised):
    """0 NOMINAL · 1 AUTHORIZED MAINTENANCE · 2 PHYSICAL TAMPER · 3 CRITICAL COMPROMISE"""
    if maintenance:
        return 1
    if compromised or usb_present:
        return 3
    if door_open:
        return 2
    return 0

def send_cmd(cmd):
    with ser_lock:
        if ser is None:
            return
        try:
            ser.write((cmd + "\n").encode())
        except Exception:
            pass  # serial thread notices the dead port and reconnects

def sync_station():
    """Single place EVSE-01 changes state: inputs → state → existing handlers → Arduino."""
    with station_lock:
        s = station
        if s["usb_present"] and not s["maintenance"]:
            s["compromised"] = True
        new = station_state(s["door_open"], s["usb_present"], s["maintenance"], s["compromised"])
        if new == s["state"]:
            return
        s["state"] = new
        c = find_charger(STATION_ID)
        if new == 0:
            restore_charger(STATION_ID)
        elif new == 1:
            c.update(tamper=False, usb=False, isolated=False, trust=100,
                     state="MAINTENANCE", kw=c["base_kw"])
        elif new == 2:
            c["isolated"] = c["usb"] = False   # 3 → 2 after restore with door still open
            apply_tamper(STATION_ID, from_sensor=serial_connected)
        elif new == 3:
            apply_usb(STATION_ID)
        c["door_open"] = s["door_open"]
        send_cmd(STATE_CMDS[new])

def handle_distance(dist):
    global latest_distance, _door_streak
    latest_distance = dist
    with station_lock:
        is_open = dist > DOOR_THRESHOLD_CM
        if is_open == station["door_open"]:
            _door_streak = 0
            return
        _door_streak += 1
        if _door_streak < DOOR_DEBOUNCE:
            return
        _door_streak = 0
        station["door_open"] = is_open
        what = "opened" if is_open else "closed"
        if station["maintenance"]:
            add_event(f"[AUTH] {STATION_ID} enclosure {what} during authorized maintenance")
        elif not is_open:
            add_event(f"[INFO] {STATION_ID} enclosure {what} ({dist:.1f} cm)")
        sync_station()
        find_charger(STATION_ID)["door_open"] = is_open

# ── USB Sentinel (Raspberry Pi) ──────────────────────────────────────────────

USB_SYSFS = "/sys/bus/usb/devices"
WATCH_CLASSES = {"08": "MASS STORAGE", "03": "HID"}   # flash drives, rubber duckies

def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""

def scan_usb():
    """{key: label} for every storage/HID interface currently attached."""
    found = {}
    for iface in glob.glob(f"{USB_SYSFS}/*:*"):
        cls = _read(f"{iface}/bInterfaceClass")
        if cls not in WATCH_CLASSES:
            continue
        dev = os.path.basename(iface).split(":")[0]
        base = f"{USB_SYSFS}/{dev}"
        vid, pid = _read(f"{base}/idVendor"), _read(f"{base}/idProduct")
        name = _read(f"{base}/product") or "Unknown device"
        found[f"{dev}:{vid}:{pid}"] = f"{name} [{WATCH_CLASSES[cls]}] {vid}:{pid}"
    return found

def usb_monitor_thread():
    if not os.path.isdir(USB_SYSFS):
        add_event("[INFO] USB monitor unavailable on this host (requires Linux) — use Simulation controls")
        return
    trusted = set(scan_usb())   # devices present at boot (e.g. your keyboard) are baseline
    add_event(f"[OK] USB monitor armed — {len(trusted)} device(s) present at startup trusted")
    seen = {}
    while True:
        now = {k: v for k, v in scan_usb().items() if k not in trusted}
        if now.keys() != seen.keys():
            with station_lock:
                for k in now.keys() - seen.keys():
                    tag = "[AUTH]" if station["maintenance"] else "[CRIT]"
                    add_event(f"{tag} USB device inserted on control unit: {now[k]}")
                for k in seen.keys() - now.keys():
                    add_event(f"[INFO] USB device removed: {seen[k]}")
                station["usb_present"] = bool(now)
                station["usb_devices"] = sorted(set(now.values()))
                sync_station()
            seen = now
        time.sleep(0.5)

# ── Serial Link (Arduino) ────────────────────────────────────────────────────

ARDUINO_VIDS = {0x2341, 0x2A03, 0x1A86, 0x0403, 0x10C4}   # Arduino, CH340, FTDI, CP210x

def find_arduino_port():
    if os.environ.get("SERIAL_PORT"):
        return os.environ["SERIAL_PORT"]
    for port in serial.tools.list_ports.comports():
        desc = (port.description or "").upper()
        if port.vid in ARDUINO_VIDS or any(k in desc for k in ["ARDUINO", "CH340", "FTDI", "CP210"]):
            return port.device
    return None

def serial_thread():
    global ser, serial_connected, latest_distance
    if not SERIAL_AVAILABLE:
        add_event("[INFO] pyserial not installed — running in simulation mode")
        return
    announced = False
    while True:
        port = find_arduino_port()
        if not port:
            if not announced:
                add_event("[INFO] No Arduino detected — simulation mode (still scanning)")
                announced = True
            time.sleep(3)
            continue
        try:
            with serial.Serial(port, 9600, timeout=0.5) as s:
                time.sleep(2)   # Uno auto-resets when the port opens
                s.reset_input_buffer()
                with ser_lock:
                    ser = s
                serial_connected = True
                announced = False
                add_event(f"[OK] Arduino connected on {port}")
                last_push = 0.0
                while True:
                    line = s.readline().decode("utf-8", errors="ignore").strip()
                    if line.startswith("DIST:"):
                        try:
                            handle_distance(float(line[5:]))
                        except ValueError:
                            pass
                    if time.time() - last_push > CMD_RESEND_S:
                        send_cmd(STATE_CMDS[station["state"]])
                        last_push = time.time()
        except Exception as e:
            add_event(f"[WARN] Arduino connection lost ({e}) — reconnecting")
        with ser_lock:
            ser = None
        serial_connected = False
        latest_distance = -1.0
        time.sleep(2)

# ── Simulation Engine ────────────────────────────────────────────────────────

def simulation_thread():
    while True:
        for c in chargers:
            if not c["isolated"] and c["state"] != "ISOLATED":
                noise = random.uniform(-0.8, 0.8)
                full = c["state"] in ("FULL_TRUST", "MAINTENANCE")
                target = c["base_kw"] if full else c["base_kw"] * 0.6
                c["kw"] = round(max(0.5, min(target * 1.1, c["kw"] + noise)), 1)
        time.sleep(1)

# ── Flask Routes ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/state")
def get_state():
    s = station
    return jsonify({
        "chargers": chargers,
        "events": events[-30:],
        "serial_connected": serial_connected,
        "distance": latest_distance,
        "station": {
            "id": STATION_ID,
            "state": s["state"],
            "name": STATE_NAMES[s["state"]],
            "distance": latest_distance,
            "threshold": DOOR_THRESHOLD_CM,
            "door_open": s["door_open"],
            "usb_present": s["usb_present"],
            "usb_devices": s["usb_devices"],
            "maintenance": s["maintenance"],
            "maint_elapsed": int(time.time() - s["maint_since"]) if s["maint_since"] else 0,
            "compromised": s["compromised"],
            "led": s["state"] in (2, 3),
            "buzzer": s["state"] == 3,
            "serial_connected": serial_connected,
        },
    })

@app.route("/api/attack", methods=["POST"])
def attack():
    data = request.get_json(silent=True) or {}
    cid = data.get("id")
    atype = data.get("type")
    if cid == STATION_ID:
        # Physical node: red-team input feeds the same state machine as the hardware
        with station_lock:
            if atype == "usb":
                if station["maintenance"]:
                    add_event(f"[AUTH] {cid} simulated USB device during maintenance — authorized")
                else:
                    station["compromised"] = True
            elif atype == "tamper":
                station["door_open"] = True
            sync_station()
        return jsonify({"ok": True})
    if atype == "tamper":
        apply_tamper(cid)
    elif atype == "usb":
        apply_usb(cid)
    return jsonify({"ok": True})

@app.route("/api/restore", methods=["POST"])
def restore():
    data = request.get_json(silent=True) or {}
    cid = data.get("id")
    atype = data.get("type")
    if cid == STATION_ID:
        with station_lock:
            if atype == "usb":
                if station["usb_present"] and not station["maintenance"]:
                    add_event(f"[WARN] {cid} restore denied — remove the USB device first")
                    return jsonify({"ok": False, "error": "Remove the USB device before restoring"}), 409
                station["compromised"] = False
            elif atype == "tamper":
                station["door_open"] = False   # live sensor re-asserts within ~300 ms if still open
            sync_station()
        return jsonify({"ok": True})
    c = find_charger(cid)
    if c:
        if atype == "tamper":
            c["tamper"] = False
            c["door_open"] = False
            if not c["usb"]:
                restore_charger(cid)
            else:
                add_event(f"[WARN] {cid} Tamper cleared but USB still active")
        elif atype == "usb":
            restore_charger(cid)
    return jsonify({"ok": True})

@app.route("/api/maintenance", methods=["POST"])
def maintenance():
    data = request.get_json(silent=True) or {}
    on = bool(data.get("on"))
    if on and not hmac.compare_digest(str(data.get("code", "")), MAINT_CODE):
        add_event(f"[CRIT] {STATION_ID} maintenance authorization failed — invalid code")
        return jsonify({"ok": False, "error": "Invalid authorization code"}), 403
    with station_lock:
        if on == station["maintenance"]:
            return jsonify({"ok": True})
        station["maintenance"] = on
        station["maint_since"] = time.time() if on else None
        if on:
            station["compromised"] = False
            add_event(f"[AUTH] {STATION_ID} authorized maintenance session started — alerts suppressed")
        else:
            add_event(f"[AUTH] {STATION_ID} maintenance session ended — sensors re-armed")
        sync_station()
    return jsonify({"ok": True})

# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    add_event("[OK] Dashboard started — 8 chargers online")
    threading.Thread(target=serial_thread, daemon=True).start()
    threading.Thread(target=usb_monitor_thread, daemon=True).start()
    threading.Thread(target=simulation_thread, daemon=True).start()

    print("\n  [+] EV Charger Security Dashboard")
    print("  ---------------------------------")
    print("  Open: http://localhost:5000")
    print("  Press Ctrl+C to stop\n")

    webbrowser.open("http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
