# LSS – Light Sensing System

Adaptive LED lighting built on an Arduino Mega 2560. The LED dims or brightens according to ambient light, switches off when no motion has been detected for 10 seconds, and can be controlled and monitored from a Python terminal GUI over USB serial.

## Features

- **Auto mode:** LED brightness is inversely mapped to the light sensor reading (dark room → bright LED)
- **Manual mode:** fixed LOW / MEDIUM / HIGH brightness
- **Motion-based auto-off:** LED turns off after 10 s without motion (all modes) and comes back on when motion is detected
- **Faster recalibration on motion:** auto mode re-reads the light sensor every 2 s after motion, otherwise every 5 s
- **Terminal GUI (Windows):** power/mode control, live Arduino messages, COM port switching, system restart
- **Power logging:** estimated power sampled every 10 s to CSV (30-day retention), with stats, text graphs and savings in DKK

## Repository structure

```
.
├── README.md              # This file
├── USER_GUIDE.md          # End-user guide
├── GUI/                   # Python terminal GUI
│   ├── lss_gui.py         # Application (LSSController + LSSApp)
│   ├── uart.py            # pyserial wrapper with Arduino auto-detection
│   ├── lss_power_log.csv  # Power log (generated at runtime)
│   └── README.md          # GUI documentation
└── LSS/                   # PlatformIO firmware project
    ├── platformio.ini     # env: megaatmega2560, Arduino framework
    ├── README.md          # Firmware documentation
    └── src/
        ├── main.cpp               # Command parsing, modes, timing
        ├── microcontroller.cpp/.h # Wraps sensors + light
        ├── lys.cpp/.h             # LED (PWM) control
        ├── lyssensor.cpp/.h       # Light sensor (LDR)
        └── motion.cpp/.h          # PIR motion sensor
```

## Hardware

| Component | Arduino Mega 2560 pin |
|-----------|-----------------------|
| LDR (light sensor) | A0 |
| PIR motion sensor  | A2 (read digitally) |
| LED                | D9 (PWM) |

Connect the board to the PC via USB (serial at 9600 baud).

## Getting started

### Requirements

- [PlatformIO](https://platformio.org/) (CLI or VS Code extension)
- Python 3 with `pyserial`
- Windows (the GUI uses `msvcrt` for keyboard input)
- Optional: `colorama` (colors), `psutil` (extra info in *System Info*)

### 1. Build and upload firmware

```powershell
cd LSS
pio run --target upload
```

Optionally monitor the serial output with `pio device monitor`.

### 2. Run the GUI

```powershell
pip install pyserial
cd GUI
python lss_gui.py
```

The GUI auto-detects the Arduino (description containing "arduino"/"USB Serial Device", or VID `2341`/`2a03`). If it isn't found, set the port under **Settings → Change COM port**.

### 3. Use it

Select **Power on / off** in the main menu. The system starts in auto mode.

| Key | Action |
|-----|--------|
| ↑ / ↓ | Navigate |
| → | Select |
| ← | Back |
| Space | Toggle detailed status / Arduino messages |
| Q | Quit |

```
Main Menu
├── Power on / off
├── Settings >      Manual (LOW/MEDIUM/HIGH), Automatic, Change COM port, Restart System
├── Data >          View Monitoring Data, View Power Graphs, Clear old data (keep last 7 days)
├── System Info >
└── Help >
```

## Serial protocol

9600 baud, ASCII, commands terminated with `\n`. Manual and auto commands require the system to be powered on; otherwise the firmware replies `ERROR: System not powered on`.

| Command (PC → Arduino) | Effect | Reply |
|------------------------|--------|-------|
| `POWER ON` | Power on, enable auto mode | `POWER ON - AUTO MODE` |
| `POWER OFF` | Power off, LED off | `POWER OFF OK` |
| `AUTO` | Switch to auto mode | `AUTO MODE ENABLED` |
| `SET INTENSITY LOW` | Fixed PWM 85 (~33 %) | `INTENSITY LOW OK` |
| `SET INTENSITY MEDIUM` | Fixed PWM 170 (~67 %) | `INTENSITY MEDIUM OK` |
| `SET INTENSITY HIGH` | Fixed PWM 255 (100 %) | `INTENSITY HIGH OK` |

Unrecognised commands are answered with `UNKNOWN: <command>`.

Other messages from the Arduino:

- `INIT OK` / `System ready - waiting for POWER ON` – on boot
- `Motion detected` – rising edge on the PIR sensor
- `Light sensor: <value> lux | LED brightness: <n>%` – each auto recalibration
- `LED brightness recalibrated` – recalibration triggered by motion
- `AUTO LED OFF - No motion detected for 10 seconds`

## How the firmware works

All logic lives in `loop()` in `main.cpp` (runs every ~50 ms):

1. **Read serial commands** into a 64-byte buffer and update `powerOn`, `autoMode` and `manualOverride`.
2. **Motion:** when powered on, a LOW→HIGH transition on A2 resets the inactivity timer.
3. **Auto-off:** if no motion for `AUTO_OFF_DELAY` (10 000 ms), the LED is set to 0. The system stays powered on.
4. **Auto mode:** while there has been recent motion, read A0 every 2 s (after motion) or 5 s and set
   `PWM = constrain(map(reading, 375, 975, 255, 0), 0, 255)`.
5. **Manual mode:** while there has been recent motion, re-apply the selected level.

> **Note:** the light value is the raw `analogRead()` result (0–1023). It is labelled "lux" in the code and output but is not calibrated to real lux.

Tunable values in `main.cpp`:

| What | Where |
|------|-------|
| Auto-off delay | `AUTO_OFF_DELAY` |
| Light range for mapping | `map(luxLevel, 375, 975, 255, 0)` |
| Recalibration intervals | `motionDetectedSinceLastUpdate ? 2000 : 5000` |
| Manual levels | `BrightnessLevel` enum in `lys.h` |

## Power estimates and logging

The GUI does not measure power. It estimates it from the current mode:

| Mode | Estimated power |
|------|-----------------|
| LOW | 0.02 W |
| MEDIUM | 0.06 W |
| HIGH | 0.10 W |
| AUTO | 0.06 W (assumed average) |

Samples are written to `GUI/lss_power_log.csv` (`timestamp,power_watts`) every 10 s while powered on. Savings are calculated against 100 % brightness 24/7 (2.4 Wh/day) at 1.8 DKK/kWh.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `No module named 'serial'` | `pip install pyserial` |
| Arduino not detected / port access denied | Close the PlatformIO monitor or Arduino IDE, then set the COM port manually in Settings |
| LED doesn't turn on | Make sure the system is powered on and trigger the PIR sensor; the LED is off after 10 s without motion |
| GUI crashes on Linux/macOS | Not supported – keyboard input is Windows-only |

## Further documentation

- [USER_GUIDE.md](USER_GUIDE.md) – end-user guide
- [GUI/README.md](GUI/README.md) – GUI details
- [LSS/README.md](LSS/README.md) – firmware details

## License

Educational group project.
