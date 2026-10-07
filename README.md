# EV Charger Security Dashboard (3D Cyberpunk Edition)

A futuristic, 3D military-command-center-style dashboard to monitor a fleet of 8 EVSE chargers for physical tamper and USB injection attacks.

Built with **Python (Flask)** and **Three.js**.

## Features
- **3D Isometric Grid**: 8 glowing charger boxes rendered in WebGL that change colors and animate based on threat state.
- **Glassmorphism UI**: Frosted glass panels with neon glow effects.
- **Red Team Injection**: Simulate physical tampering and USB injection attacks.
- **Auto-Isolation**: USB injection automatically isolates the charger after a 3-second countdown.
- **Hardware Integration**: Optionally connects to an Arduino over serial to detect physical door opening via an ultrasonic sensor (HC-SR04).

## Tech Stack
- **Backend**: Python, Flask, PySerial (optional)
- **Frontend**: HTML5, CSS3, JavaScript (ES6+), Three.js (via CDN)

## How to Run

1. Ensure you have Python installed.
2. Install dependencies:
   ```bash
   pip install Flask pyserial
   ```
   *(Note: `pyserial` is only strictly required if you are connecting an Arduino).*
3. Run the backend server:
   ```bash
   python dashboard.py
   ```
4. The dashboard will automatically open in your default browser at `http://localhost:5000`.

## Simulation Mode
If no Arduino is detected, the dashboard falls back to Simulation Mode, allowing you to trigger threats entirely via the UI's Red Team panel.
