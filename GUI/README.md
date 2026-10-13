# LSS GUI – Python Terminal Interface

Windows terminal GUI for controlling and monitoring the LSS firmware over USB serial.

## Files

| File | Purpose |
|------|---------|
| `lss_gui.py` | Application: `LSSController` (serial, state, logging) and `LSSApp` (menus, rendering) |
| `uart.py` | `UARTReader` – pyserial wrapper with Arduino port auto-detection |
| `lss_power_log.csv` | Power log, created/updated at runtime |

## Requirements

- Windows (keyboard input uses `msvcrt`; the GUI raises an error on Linux/macOS)
- Python 3
- `pyserial` – **required**
- `colorama` – optional, better ANSI color support
- `psutil` – optional, CPU/RAM/disk info under *System Info*

```powershell
pip install pyserial
pip install colorama psutil   # optional
```

## Running

```powershell
cd GUI
python lss_gui.py
```

On startup the GUI tries to find the Arduino automatically (see [Serial connection](#serial-connection)). Select **Power on / off** to start the system in auto mode.

## Controls

| Key | Action |
|-----|--------|
| ↑ / ↓ | Navigate |
| → | Select |
| ← | Back / cancel input |
| Space | Show/hide detailed status and recent Arduino messages |
| Q / Ctrl+C | Quit (sends `POWER OFF` if the system is on) |

## Menus

```
Main Menu
├── Power on / off
├── Settings >
│   ├── Manual >          Low / Medium / High
│   ├── Automatic >       Yes / No
│   ├── Change COM port > type the port number, e.g. 5 for COM5
│   └── Restart System >  sends POWER OFF, then POWER ON (back in auto mode)
├── Data >
│   ├── View Monitoring Data
│   ├── View Power Graphs
│   └── Clear old data (keep last 7 days)
├── System Info >
└── Help >
```

## Status display

The header shows power, mode, intensity and Arduino connection state, plus up to the 3 latest alerts (e.g. "Arduino disconnected", savings milestones). With Space toggled on, incoming Arduino lines are grouped as:

- **Events** – lines containing `motion detected`
- **Sensors** – lines containing `light sensor` or `lux`
- **Other** – recalibration / LED brightness lines

## Serial connection

- 9600 baud, ASCII, commands sent with a trailing `\n`, read timeout 0.5 s
- Auto-detection picks the first port whose description or manufacturer contains `arduino` or `usb serial device`, or whose hardware ID contains VID `2341` / `2a03`
- If the port is lost, the background reader keeps trying to reconnect. A disconnect alert is shown at most once per minute
- Commands sent by the GUI: `POWER ON`, `POWER OFF`, `AUTO`, `SET INTENSITY LOW|MEDIUM|HIGH` (see the [main README](../README.md#serial-protocol))

## Power data

Power is **estimated** from the selected mode, not measured:

| Mode | Watts |
|------|-------|
| low | 0.02 |
| medium | 0.06 |
| high | 0.10 |
| auto | 0.06 (assumed average) |

- A background thread samples every 10 s while the system is powered on
- Samples are stored in `lss_power_log.csv` as `timestamp,power_watts`. Entries older than 30 days are dropped
- *View Monitoring Data* shows current power, last-hour average, 24 h min/max/average, energy used, and savings against 100 % brightness 24/7 (0.1 W → 2.4 Wh/day) at 1.8 DKK/kWh
- *Clear old data* removes samples older than 7 days (after `y` confirmation)

## Threads

| Thread | Job |
|--------|-----|
| Main | Keyboard input and rendering (polls with timeout so the header updates live) |
| Serial reader | Reads lines, categorizes them, reconnects on errors |
| Data sampler | Logs power every 10 s |

The background threads are daemon threads and are stopped on exit via `reader_stop` / `data_stop` events.

## Customization

| Change | Where |
|--------|-------|
| Power per mode | `mapping` dict in `LSSController.get_data_stats()` (and the estimate in the header) |
| Sample interval | `interval` in `LSSController._data_loop()` |
| Retention | `timedelta(days=30)` in `LSSController` |
| Electricity price | search for `1.8` |

## Known limitations

- Windows only
- One Arduino at a time, local USB only
- Power figures are estimates based on mode, not measurements
- Some in-app *Help* pages mention features that aren't implemented (CSV/JSON export, brightness lock, "Serial settings → Reconnect")

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `No module named 'serial'` | `pip install pyserial` |
| "Arduino: NOT connected" | Check the USB cable; close Arduino IDE / `pio device monitor`; set the port in *Settings → Change COM port* |
| No colors / garbled output | Use Windows Terminal or PowerShell, optionally install `colorama` |
| No data in *Data* menu | The system must be powered on; first sample is logged within 10 s |

## See also

- [Main README](../README.md)
- [Firmware README](../LSS/README.md)
- [User guide](../USER_GUIDE.md)
