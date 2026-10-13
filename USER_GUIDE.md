# LSS User Guide

## Quick start

1. **Upload the firmware:** `cd LSS` then `pio run --target upload`
2. **Install and start the GUI:** `pip install pyserial` then `python GUI/lss_gui.py`
3. **Power on:** choose **Power on / off** in the main menu
4. The system starts in **automatic mode**. Move in front of the motion sensor to turn the light on

## How it works

### Automatic mode (default)

- The LED gets brighter when the room is dark and dimmer when it's bright
- Light sensor reading 375 or below → LED at 100 %. Reading 975 or above → LED off
- The light is re-checked every 5 seconds, or every 2 seconds right after motion

### Manual mode

Choose **Settings → Manual**:

| Level | Brightness | Estimated power |
|-------|-----------|-----------------|
| Low | ~33 % | 20 mW |
| Medium | ~67 % | 60 mW |
| High | 100 % | 100 mW |

The brightness stays fixed, but the motion timer still applies.

### Motion timer (all modes)

- With no motion for **10 seconds**, the LED turns off
- The system stays on. The LED comes back as soon as motion is detected

## GUI navigation

| Key | Action |
|-----|--------|
| ↑ / ↓ | Move in menu |
| → | Select |
| ← | Back |
| Space | Show/hide details and Arduino messages |
| Q | Quit (also powers the system off) |

## Data and savings

The **Data** menu shows:

- Current power usage
- Last-hour average
- 24-hour min / max / average
- Savings compared with running at 100 % brightness all day (2.4 Wh/day)
- Savings in DKK at 1.8 DKK/kWh

Power is estimated from the selected mode, and automatic mode counts as 60 mW. Data is saved every 10 seconds to `GUI/lss_power_log.csv` while the system is on and kept for 30 days. **Clear old data** removes everything older than 7 days.

**Example:** at an average of 60 mW, you use 1.44 Wh/day instead of 2.4 Wh/day. That's a 40 % saving (0.96 Wh/day).

## Hardware setup

```
Arduino Mega 2560
├── A0: Light sensor (LDR)
├── A2: Motion sensor (PIR)
└── D9: LED (PWM)
```

## Troubleshooting

**Arduino not detected?**
- Check the USB cable and find the port in Device Manager under "Ports (COM & LPT)"
- Close Arduino IDE or other serial monitors
- Set the port in **Settings → Change COM port** (type just the number, e.g. `5`)

**LED not turning on?**
- Make sure the system is powered on
- Wave in front of the motion sensor. The LED is off after 10 s without motion
- Check the wiring: LDR on A0, PIR on A2, LED on D9
- Try **Settings → Restart System**

**LED turns off by itself?**
- Normal. It turns off after 10 seconds without motion and comes back on motion

**No data shown?**
- The system must be powered on. The first sample is logged within 10 seconds
- Make sure the `GUI` folder is writable

## Serial commands (for debugging)

Open a serial monitor at 9600 baud (e.g. `pio device monitor`) and send:

| Command | Effect |
|---------|--------|
| `POWER ON` | Power on in automatic mode |
| `POWER OFF` | Power off, LED off |
| `AUTO` | Automatic mode (requires power on) |
| `SET INTENSITY LOW` / `MEDIUM` / `HIGH` | Manual brightness (requires power on) |
