# LSS Firmware – Arduino Mega 2560

PlatformIO firmware for the LSS adaptive lighting controller. It reads an LDR and a PIR sensor, drives an LED with PWM and takes commands from the GUI over serial.

## Hardware

| | |
|---|---|
| Board | Arduino Mega 2560 (ATmega2560, 16 MHz, 8 KB SRAM, 256 KB flash) |
| LDR (light) | A0 – `analogRead()`, 0–1023 |
| PIR (motion) | A2 – read with `digitalRead()` |
| LED | D9 – PWM 0–255 |
| Serial | USB, 9600 baud |

## Files

```
LSS/
├── platformio.ini            # env:megaatmega2560, framework arduino, monitor_speed 9600
└── src/
    ├── main.cpp              # Command parsing, power/mode state, motion timer, auto mapping
    ├── microcontroller.cpp/.h# Microcontroller class – owns Light, Lyssensor, MotionSensor
    ├── lys.cpp/.h            # Light class + BrightnessLevel enum (_LOW/_MEDIUM/_HIGH)
    ├── lyssensor.cpp/.h      # Lyssensor – getLuxLevel() returns analogRead()
    └── motion.cpp/.h         # MotionSensor – isMotionDetected()
```

`main.cpp` only uses `Microcontroller::manuelTurnOn()` and `resetCounter()`. It reads A0 and A2 directly. `Microcontroller::loop()` (an older stand-alone control loop with a 5 s timer) is not called.

## Build and upload

```powershell
cd LSS
pio run                    # build
pio run --target upload    # upload
pio device monitor         # serial monitor at 9600 baud
```

Ports are auto-detected. To force one, uncomment `upload_port` / `monitor_port` in `platformio.ini`.

## Serial protocol

Commands are ASCII lines terminated by `\n` or `\r` (max 63 characters). Matching is prefix-based and case-sensitive.

| Command | Requires power | Effect | Reply |
|---------|:-:|--------|-------|
| `POWER ON` | – | `powerOn = true`, auto mode, start motion timer | `POWER ON - AUTO MODE` |
| `POWER OFF` | – | Power off, LED to 0 | `POWER OFF OK` |
| `AUTO` | yes | Auto mode, reset motion timer | `AUTO MODE ENABLED` |
| `ENABLE AUTO` | no* | Auto mode | `AUTO MODE ENABLED` |
| `SET INTENSITY LOW` | yes | Manual, PWM 85 | `INTENSITY LOW OK` |
| `SET INTENSITY MEDIUM` | yes | Manual, PWM 170 | `INTENSITY MEDIUM OK` |
| `SET INTENSITY HIGH` | yes | Manual, PWM 255 | `INTENSITY HIGH OK` |

\* `ENABLE AUTO` does not check `powerOn`, but the LED is only driven while powered on.

Without power, manual/`AUTO` commands reply `ERROR: System not powered on`. Anything else replies `UNKNOWN: <command>`.

Other output:

| Message | When |
|---------|------|
| `INIT OK`, `System ready - waiting for POWER ON` | After boot (2 s delay) |
| `Motion detected` (printed twice) | LOW→HIGH on the PIR |
| `Light sensor: <n> lux \| LED brightness: <p>%` | Every auto recalibration |
| `LED brightness recalibrated` | Recalibration following motion |
| `AUTO LED OFF - No motion detected for 10 seconds` | LED switched off by timeout |

## Behaviour

`loop()` runs roughly every 50 ms (`delay(50)` at the end):

1. **Commands** – read and handle any complete serial lines.
2. **Motion** (only when powered on) – a rising edge on A2 sets `lastMotionTime = millis()` and flags `motionDetectedSinceLastUpdate`.
3. **Auto-off** (all modes) – if `millis() - lastMotionTime >= AUTO_OFF_DELAY` (10 000 ms), the LED is set to 0. The system stays powered on.
4. **Auto mode** – while motion is recent, every 2 s (if motion was flagged) or 5 s:
   ```cpp
   int targetPWM = constrain(map(luxLevel, 375, 975, 255, 0), 0, 255);
   ```
   Reading ≤ 375 → full brightness, ≥ 975 → off. The value is applied immediately (no fade).
5. **Manual mode** – while motion is recent, the selected level is re-applied so the LED turns back on after a timeout.

On boot the system is off. `POWER ON` always starts in auto mode.

> The "lux" value is the raw 10-bit ADC reading, not a calibrated lux value. 375–975 is the range the mapping was tuned for.

### Brightness levels

Defined in `lys.h`:

```cpp
enum BrightnessLevel {
    _LOW = 85,      // ~33 %
    _MEDIUM = 170,  // ~67 %
    _HIGH = 255     // 100 %
};
```

## Customization (`main.cpp`)

| Setting | Code |
|---------|------|
| Auto-off delay | `const unsigned long AUTO_OFF_DELAY = 10000;` |
| Light range | `map(luxLevel, 375, 975, 255, 0)` |
| Recalibration intervals | `motionDetectedSinceLastUpdate ? 2000 : 5000` |
| Loop period | `delay(50);` |
| Pins | `#define PWMPIN 9`, `MOTIONPIN A2`, `LUXPIN A0` |

Note: the auto-off message text says "10 seconds" literally, so update it if you change `AUTO_OFF_DELAY`.

## Known limitations

- One LED, no fading in auto/manual mode (`Light::regulateBrightness()` supports stepping but isn't used by `main.cpp`)
- No EEPROM – mode and settings reset on power cycle
- `STATUS`-style queries are not implemented
- Light reading is uncalibrated ADC, not lux

## Troubleshooting

| Problem | Check |
|---------|-------|
| LED never turns on | Send `POWER ON`, then trigger the PIR – the LED is off whenever there has been no motion for 10 s |
| Motion not detected | PIR power/wiring on A2; PIR modules typically need 30–60 s warm-up |
| Brightness doesn't change in auto | Wiring on A0; watch `Light sensor:` lines in the monitor |
| `UNKNOWN:` replies | Commands are case-sensitive and must end with a newline |
| Upload fails | Close the GUI / serial monitor holding the COM port |

## See also

- [Main README](../README.md)
- [GUI README](../GUI/README.md)
- [User guide](../USER_GUIDE.md)
