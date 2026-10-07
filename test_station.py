"""Run: python test_station.py — checks the EVSE-01 state machine and latch."""
import dashboard as d

# Truth table: maintenance > compromise/USB > door > nominal
assert d.station_state(False, False, False, False) == 0
assert d.station_state(True,  False, False, False) == 2
assert d.station_state(True,  True,  False, False) == 3
assert d.station_state(False, False, False, True)  == 3
assert d.station_state(True,  True,  True,  True)  == 1

s, c = d.station, d.find_charger("EVSE-01")
sent = []
d.send_cmd = sent.append

# Door open → tamper, LED only
s["door_open"] = True; d.sync_station()
assert s["state"] == 2 and c["state"] == "THROTTLED" and sent[-1] == "CMD:LED_ON"

# USB in → compromise, latched even after unplug
s["usb_present"] = True; d.sync_station()
assert s["state"] == 3 and c["state"] == "ISOLATED" and sent[-1] == "CMD:ALARM_ON"
s["usb_present"] = False; d.sync_station()
assert s["state"] == 3, "compromise must latch until operator restore"

# Operator restore with door still open → back to tamper, not nominal
s["compromised"] = False; d.sync_station()
assert s["state"] == 2 and c["state"] == "THROTTLED"

# Maintenance suppresses everything, even a fresh USB
s["maintenance"] = True; s["usb_present"] = True; d.sync_station()
assert s["state"] == 1 and c["state"] == "MAINTENANCE" and sent[-1] == "CMD:ALARM_OFF"
assert not s["compromised"]

# Maintenance off with USB still in → compromise re-triggers
s["maintenance"] = False; d.sync_station()
assert s["state"] == 3 and s["compromised"]

# Everything clear → nominal
s.update(usb_present=False, door_open=False, compromised=False); d.sync_station()
assert s["state"] == 0 and c["state"] == "FULL_TRUST" and sent[-1] == "CMD:ALARM_OFF"

# Door debounce: 2 noisy readings don't flip it, 3 do
for _ in range(2): d.handle_distance(25.0)
assert not s["door_open"]
d.handle_distance(25.0)
assert s["door_open"] and s["state"] == 2

print("station state machine: all checks passed")
