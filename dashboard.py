"""
EV Charger Security Dashboard — Flask Backend
Manages 8 EVSE chargers, Arduino serial, simulation engine, and REST API.
"""

from flask import Flask, render_template, jsonify, request
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
latest_distance = -1.0
serial_connected = False

# ── Helpers ───────────────────────────────────────────────────────────────────

def add_event(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    events.append({"time": ts, "msg": msg})
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
    source = "SENSOR" if from_sensor else "RED TEAM"
    add_event(f"⚠ {cid} CABINET TAMPER [{source}] — Trust → 60")

def apply_usb(cid):
    c = find_charger(cid)
    if not c:
        return
    c["usb"] = True
    c["trust"] = 0
    c["state"] = "ISOLATED"
    c["kw"] = 0.0
    c["isolated"] = True
    add_event(f"🔴 {cid} USB INJECTION DETECTED — Auto-isolating in 3s...")
    threading.Timer(3.0, finalize_isolation, args=[cid]).start()

def finalize_isolation(cid):
    c = find_charger(cid)
    if c and c["usb"]:
        add_event(f"🔒 {cid} DISCONNECTED — Charger isolated from grid")

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
    add_event(f"✅ {cid} RESTORED — Full trust, back online")

# ── Serial Reader (Arduino) ─────────────────────────────────────────────────

def find_arduino_port():
    if not SERIAL_AVAILABLE:
        return None
    ports = serial.tools.list_ports.comports()
    for port in ports:
        if any(k in port.description.upper() for k in ["ARDUINO", "CH340", "FTDI", "CP210"]):
            return port.device
    return None

def handle_door_sensor(dist):
    c = chargers[0]  # EVSE-01
    if dist > 10.0 and not c["door_open"]:
        c["door_open"] = True
        if not c["tamper"]:
            apply_tamper("EVSE-01", from_sensor=True)
    elif dist <= 10.0 and c["door_open"]:
        c["door_open"] = False
        if c["tamper"] and not c["usb"]:
            restore_charger("EVSE-01")

def serial_thread():
    global latest_distance, serial_connected
    port = find_arduino_port()
    if not port:
        add_event("📡 No Arduino detected — running in SIMULATION mode")
        return
    try:
        ser = serial.Serial(port, 9600, timeout=1)
        time.sleep(2)
        serial_connected = True
        add_event(f"🔌 Arduino connected on {port}")
        while True:
            if ser.in_waiting > 0:
                line = ser.readline().decode("utf-8", errors="ignore").strip()
                if line.startswith("DIST:"):
                    try:
                        dist = float(line.split(":")[1])
                        latest_distance = dist
                        handle_door_sensor(dist)
                    except ValueError:
                        pass
            time.sleep(0.05)
    except Exception as e:
        serial_connected = False
        add_event(f"⚠ Serial error: {e}")

# ── Simulation Engine ────────────────────────────────────────────────────────

def simulation_thread():
    while True:
        for c in chargers:
            if not c["isolated"] and c["state"] != "ISOLATED":
                noise = random.uniform(-0.8, 0.8)
                target = c["base_kw"] if c["state"] == "FULL_TRUST" else c["base_kw"] * 0.6
                c["kw"] = round(max(0.5, min(target * 1.1, c["kw"] + noise)), 1)
        time.sleep(1)

# ── Flask Routes ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/state")
def get_state():
    return jsonify({
        "chargers": chargers,
        "events": events[-30:],
        "serial_connected": serial_connected,
        "distance": latest_distance,
    })

@app.route("/api/attack", methods=["POST"])
def attack():
    data = request.json
    cid = data.get("id")
    atype = data.get("type")
    if atype == "tamper":
        apply_tamper(cid)
    elif atype == "usb":
        apply_usb(cid)
    return jsonify({"ok": True})

@app.route("/api/restore", methods=["POST"])
def restore():
    data = request.json
    cid = data.get("id")
    atype = data.get("type")
    c = find_charger(cid)
    if c:
        if atype == "tamper":
            c["tamper"] = False
            c["door_open"] = False
            if not c["usb"]:
                restore_charger(cid)
            else:
                add_event(f"⚠ {cid} Tamper cleared but USB still active")
        elif atype == "usb":
            restore_charger(cid)
    return jsonify({"ok": True})

# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    add_event("🚀 Dashboard initialized — 8 EVSE nodes online")
    threading.Thread(target=serial_thread, daemon=True).start()
    threading.Thread(target=simulation_thread, daemon=True).start()

    print("\n  [+] EV Charger Security Dashboard")
    print("  ---------------------------------")
    print("  Open: http://localhost:5000")
    print("  Press Ctrl+C to stop\n")

    webbrowser.open("http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
