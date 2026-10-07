# GridSentinel — EV Charging Security Dashboard

A cyber-physical security testbed: a real Smart EV charging station (EVSE-01, Arduino + Raspberry Pi) plus 7 simulated chargers, monitored from an enterprise-style operations dashboard (single-line diagram, alert queue, audit log with CSV export).

Built with **Python (Flask)**, plain HTML/CSS/JS (no build step), and an **Arduino Uno**.

## Station states (EVSE-01)

| State | Trigger | Dashboard | Station | LED D12 | Buzzer D6 |
|---|---|---|---|---|---|
| **S0 Nominal** | door ≤ 10 cm, no USB, maintenance off | green status | online | off | off |
| **S1 Authorized maintenance** | maintenance ON (auth code) | violet banner, alerts suppressed | online | off | off |
| **S2 Physical tamper** | door > 10 cm, maintenance off | warning banner, alert raised | online (throttled) | **solid** | off |
| **S3 Critical compromise** | unauthorized USB storage/HID on the Pi | critical banner, breaker Q1 shown open | **offline** | **on** | **pulsing** |

S3 **latches**. The station stays offline until the USB device is removed **and** an operator clicks *Restore station* (or opens a maintenance session). Ending maintenance re-arms the sensors immediately, so an open door or a plugged-in USB re-triggers its alert.

## Hardware

```
Arduino Uno                     Raspberry Pi
  D10 ── HC-SR04 TRIG             USB ── Arduino (serial, 9600 baud)
  D11 ── HC-SR04 ECHO             USB ── "attack" port (flash drive / rubber ducky)
  D6  ── active buzzer (+)
  D12 ── 220Ω ── red LED
```

Flash `arduino/cyberpunk_ev_hardware/cyberpunk_ev_hardware.ino` with the Arduino IDE.

**Serial protocol**

| Direction | Message | Meaning |
|---|---|---|
| Arduino → Pi | `DIST:<cm>` every 100 ms | median of 3 pings. `999.0` = no echo or cut wire (treated as tamper) |
| Pi → Arduino | `CMD:LED_ON` | S2: LED solid |
| Pi → Arduino | `CMD:ALARM_ON` | S3: LED + buzzer |
| Pi → Arduino | `CMD:ALARM_OFF` | S0/S1: everything off |

The Pi is the state authority. It re-sends the current command every 2 s, so an Arduino reset never leaves the alarm in the wrong state.

## Run

```bash
pip install Flask pyserial
python dashboard.py
```

Open `http://<pi-ip>:5000` from a laptop browser.

| Env var | Default | Purpose |
|---|---|---|
| `MAINT_CODE` | `7777` | Government maintenance authorization code. **Change it before the demo.** |
| `SERIAL_PORT` | auto-detect | Force the Arduino port, e.g. `/dev/ttyACM0` |

Pi notes:
- Add your user to `dialout` (`sudo usermod -aG dialout $USER`, then log out and back in).
- USB devices present **at startup** are trusted (baseline). Plug your keyboard in before launching, or use SSH. A keyboard plugged in mid-demo counts as an HID attack.

Calibration knobs: `DOOR_THRESHOLD_CM` / `DOOR_DEBOUNCE` in `dashboard.py`, and `US_PER_CM` / `SAMPLE_MS` in the sketch.

## Simulation mode

With no Arduino, or on a non-Linux host, the dashboard runs in simulation mode. Use **Simulation controls** to open an enclosure or insert a USB device on any charger. On EVSE-01 those buttons feed the same state machine as the real hardware.

## Demo script

1. Open the enclosure → **S2** warning banner, LED solid, the distance chart crosses the trip line.
2. Plug in a flash drive → **S3** critical banner, LED + buzzer, breaker Q1 opens on the diagram.
3. *Restore station* is **disabled** while the drive is in. Unplug it, then restore → back to S0 (or S2 if the door is still open).
4. Enter the maintenance code → **S1**. Open the door and plug in a USB: no alerts, hardware silent.
5. End the session with the door open → S2 again.

## Test

```bash
python test_station.py
```
