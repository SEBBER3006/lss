"""LSS Terminal GUI (Windows-focused)

This file implements a simple terminal UI for the LSS lighting controller.
It is intentionally focused on Windows consoles (PowerShell / Windows Terminal).

- Background serial reader (uses `UARTReader`) that stores recent raw lines.
- A header showing parsed status, categorized events, and recent Arduino messages.
- Live updates via polling input with a timeout (so status updates without user input).

Notes for maintainers:
- This module avoids Unix-specific termios/tty/select code and targets Windows input APIs
    (`msvcrt`). If you need cross-platform support, reintroduce the Unix branches carefully.

Terminology:
- ACK: short for acknowledgment — brief reply lines from the Arduino that confirm
    commands (for example: "POWER ON" -> "Power is now ON"). These are shown under
    the 'ACKs' section in the header when enabled.
- Raw: the unparsed raw ASCII lines received from the Arduino. These are kept in
    `self.recent_raw` and shown in the header when status is visible (toggle with SPACE).
"""

import os
import platform
import time
import sys
import serial
import threading
import queue
import json
import shutil
from collections import deque
from uart import UARTReader
from datetime import datetime, timedelta

# Enable ANSI escape sequence processing on Windows so colors work in modern consoles.
if platform.system() == 'Windows':
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        STD_OUTPUT_HANDLE = -11
        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            new_mode = mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
            kernel32.SetConsoleMode(handle, new_mode)
    except Exception:
        pass

# Use colorama on Windows to improve ANSI handling across consoles.
try:
    from colorama import init as _colorama_init
    _colorama_init()
except Exception:
    # colorama is optional; if not installed we still attempt to enable VT mode above.
    pass


def _colorize_tokens(s: str) -> str:
    """Apply token-based coloring to a string.

    Tokens colored (case-insensitive):
    - 'detected' -> green
    - 'FAIL' / 'ERROR' -> red
    - 'OK' -> cyan
    - 'INIT' -> yellow
    """
    try:
        import re

        def repl(m):
            t = m.group(0)
            tl = t.lower()
            if tl == 'detected':
                return '\x1b[32m' + t + '\x1b[0m'
            if tl in ('fail', 'error'):
                return '\x1b[31m' + t + '\x1b[0m'
            if tl == 'ok':
                return '\x1b[36m' + t + '\x1b[0m'
            if tl == 'init':
                return '\x1b[33m' + t + '\x1b[0m'
            return t

        # Replace whole-word tokens only
        return re.sub(r"\b(detected|fail|error|ok|init)\b", repl, s, flags=re.IGNORECASE)
    except Exception:
        return s

# Defaults (can be changed from GUI)
ARDUINO_PORT = None
BAUD = 9600
# Do not open serial port at import time — open it from the controller.

class LSSController:
    """Controller model for the LSS lighting system.

    This class represents the application-side view of the Arduino
    lighting controller. It keeps track of state (power, mode,
    intensity), holds recent raw serial messages for UI display, and
    runs background threads for serial reading and periodic sampling.

    Attributes:
        initialised (bool): True if the controller has run its init routine.
        power_on (bool): True when power is turned on.
        mode (str): 'auto' or 'manual'.
        intensity (str): 'low', 'medium', or 'high'.
        sensor_info (dict): last parsed sensor lines from the Arduino.
        last_ambient (int|None): last ambient/lux reading.
        last_motion (str|None): timestamp string of the last motion event.
        recent_raw (collections.deque): most recent raw lines from serial.
    """

    def send_arduino(self, cmd):
        """Send a command to the Arduino and read a short reply.

        Args:
            cmd (str): Command text to send (without trailing newline).

        The method writes the command followed by a newline and then
        briefly attempts to read a single-line response which is printed
        to stdout if present. Errors are printed but not raised.
        """
        if not hasattr(self, 'uart') or self.uart is None:
            print("[Arduino]: serial port not available")
            return

        try:
            self.uart.send_command(cmd)
            # brief pause for reply
            time.sleep(0.1)
            resp = self.uart.read_line()
            if resp:
                print(f"[Arduino]: {resp}")
        except Exception as e:
            print(f"[Arduino] serial error: {e}")
            # no logger: just print the error

    def __init__(self, open_serial: bool = True):
        """Create controller, attempt serial open, and start background tasks.

        The constructor attempts to open the default serial port defined by
        `ARDUINO_PORT`. If opening fails the controller remains usable for
        offline testing but `sensors_ok` will be False and the background
        reader will attempt to reconnect periodically."""

        self.initialised = False
        self.power_on = False
        self.mode = "auto"
        self.intensity = "medium"
        self.power_on_time = None  # Track when system was powered on

        # sensor/state info from Arduino
        self.init_ok = False
        self.sensor_info = {}  # e.g. {'LDR': 'OK:512', 'PIR': 'HIGH'}
        self.last_ambient = None
        self.last_motion = None
        self.sensors_ok = False
        
        # Version info
        self.gui_version = "1.0.0"
        
        # UART helper and background reader
        self.uart = None
        self.reader_thread = None
        self.reader_stop = threading.Event()
        self.reader_queue = queue.Queue()
        self.recent_raw = deque(maxlen=5)
        # Categorized message deques
        self.recent_telemetry = deque(maxlen=5)
        self.recent_sensors = deque(maxlen=5)
        self.recent_other = deque(maxlen=5)

        if open_serial:
            try:
                self.uart = UARTReader(port=ARDUINO_PORT, baud=BAUD, timeout=0.5)
                self.sensors_ok = True
            except Exception as e:
                print(f"Warning: could not open serial port {ARDUINO_PORT}: {e}")
                self.uart = None
                self.sensors_ok = False
        else:
            self.uart = None
            self.sensors_ok = False

        # Start background reader thread (will attempt reconnect if needed)
        self.start_reader()
        
        # Data logging with persistence
        self.data_log_file = "lss_power_log.csv"
        self.data_log = []  # list of (datetime, value_watts)
        self._load_data_log()  # Load existing data from file
        self.data_stop = threading.Event()
        self.data_thread = threading.Thread(target=self._data_loop, daemon=True)
        self.data_thread.start()
        # Display toggle for status bar and categorized messages (default: hidden)
        self.show_status = False
        
        # Alert/notification tracking
        self.alerts = []  # List of alert messages
        self.last_disconnect_alert = None
        self.savings_milestones = [1.0, 5.0, 10.0, 25.0, 50.0]  # DKK milestones
        self.reached_milestones = set()

    # Init / Power

    def init_system(self):
        self.initialised = True
        self.power_on = True
        self.mode = "auto"
        self.intensity = "auto"
        self.power_on_time = datetime.now()  # Track power-on time
        return True, "System is initialized and running in automatic mode."

    def toggle_power(self):
        if not self.initialised and not self.power_on:
            return self.init_system()

        self.power_on = not self.power_on
        state = "on" if self.power_on else "off"
        return True, f"System is now {state}."

    # Manual mode

    def set_manual_intensity(self, level):
        if not self.initialised or not self.power_on:
            return False, "System is not powered on/initialized."

        if level not in ("low", "medium", "high"):
            return False, "Invalid level."

        self.mode = "manual"
        self.intensity = level
        return True, f"Light intensity is now set to {level.upper()}."

    # Automatic mode

    def set_automatic(self):
        if not self.initialised or not self.power_on:
            return False, "System is not powered on/initialized."

        self.mode = "auto"
        self.intensity = "auto"
        return True, "System is now running in AUTOMATIC mode."

    # Data / monitoring

    def generate_data_if_running(self):
        if not (self.initialised and self.power_on):
            return

        # 5mm LED power consumption in watts (20-100mW typical)
        base = {"low": 0.02, "medium": 0.06, "high": 0.1, "auto": 0.06}.get(self.intensity, 0.06)
        value = base
        now = datetime.now()
        self.data_log.append((now, float(value)))
        # prune to keep last 30 days
        cutoff = now - timedelta(days=30)
        self.data_log = [e for e in self.data_log if e[0] >= cutoff]
        # Save to file every sample
        self._save_data_log()

    def get_data(self):
        """Return sampled data as a list of (timestamp_str, value).

        The timestamps are formatted as YYYY-MM-DD HH:MM:SS strings for
        easy printing in the UI. This triggers a sample if the system
        is running to ensure the latest instantaneous value is present.
        """

        self.generate_data_if_running()
        result = [(dt.strftime('%Y-%m-%d %H:%M:%S'), val) for dt, val in list(self.data_log)]
        return result

    def _data_loop(self):
        """Background sampler: records instantaneous power usage periodically when running."""
        interval = 10.0  # seconds between samples (for demo/testing)
        while not self.data_stop.is_set():
            if self.initialised and self.power_on:
                try:
                    self.generate_data_if_running()
                except Exception:
                    pass
            time.sleep(interval)

    def stop_data(self):
        try:
            self.data_stop.set()
            if self.data_thread and self.data_thread.is_alive():
                self.data_thread.join(timeout=1)
        except Exception:
            pass

    def get_data_stats(self):
        """Return statistical summaries over collected samples.

        Returns a dict containing:
        - `current_power`: estimated instantaneous power (W)
        - `hourly_avg`: average power over the last hour
        - `day_min`, `day_max`, `day_avg`: min/max/avg for last 24 hours
        - `total_wh`: approximate energy (Wh) integrated over last 24 hours
        """
        now = datetime.now()
        # 5mm LED power in watts
        mapping = {"low": 0.02, "medium": 0.06, "high": 0.1, "auto": 0.06}
        current_power = mapping.get(self.intensity, 0.0) if (self.initialised and self.power_on) else 0.0

        # hourly average (last 60 minutes)
        one_hour_ago = now - timedelta(hours=1)
        hour_entries = [e for e in self.data_log if e[0] >= one_hour_ago]
        hourly_avg = sum(v for _, v in hour_entries) / len(hour_entries) if hour_entries else 0.0

        # 24-hour stats
        day_ago = now - timedelta(days=1)
        day_entries = [e for e in self.data_log if e[0] >= day_ago]
        if not day_entries:
            day_min = day_max = day_avg = 0.0
            total_wh = 0.0
        else:
            vals = [v for _, v in day_entries]
            day_min = min(vals)
            day_max = max(vals)
            day_avg = sum(vals) / len(vals)
            # integrate energy over time using trapezoid rule
            total_wh = 0.0
            # sort entries
            seq = sorted(day_entries, key=lambda x: x[0])
            for i in range(len(seq) - 1):
                t1, p1 = seq[i]
                t2, p2 = seq[i + 1]
                dt_hours = (t2 - t1).total_seconds() / 3600.0
                total_wh += (p1 + p2) / 2.0 * dt_hours
            # add last sample to now
            if seq:
                t_last, p_last = seq[-1]
                dt_hours = (now - t_last).total_seconds() / 3600.0
                total_wh += p_last * dt_hours

        # Check savings milestones
        max_power_wh = 0.1 * 24  # 100mW for 24h = 2.4 Wh
        savings_wh = max_power_wh - total_wh
        savings_dkk = (savings_wh / 1000) * 1.8
        
        for milestone in self.savings_milestones:
            if savings_dkk >= milestone and milestone not in self.reached_milestones:
                self.reached_milestones.add(milestone)
                self.alerts.append(("SUCCESS", now, f"Milestone reached! Saved {milestone:.1f} DKK in 24h"))

        return {
            'current_power': current_power,
            'hourly_avg': hourly_avg,
            'day_min': day_min,
            'day_max': day_max,
            'day_avg': day_avg,
            'total_wh': total_wh,
        }

    # Data persistence methods

    def _save_data_log(self):
        """Save data log to CSV file for persistence."""
        try:
            import csv
            with open(self.data_log_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['timestamp', 'power_watts'])
                for dt, watts in self.data_log:
                    writer.writerow([dt.isoformat(), watts])
        except Exception as e:
            # Silent fail - don't interrupt operation
            pass

    def _load_data_log(self):
        """Load data log from CSV file if it exists."""
        try:
            import csv
            import os
            if not os.path.exists(self.data_log_file):
                return
            with open(self.data_log_file, 'r', newline='') as f:
                reader = csv.reader(f)
                next(reader)  # skip header
                for row in reader:
                    if len(row) == 2:
                        dt = datetime.fromisoformat(row[0])
                        watts = float(row[1])
                        self.data_log.append((dt, watts))
            # Prune old data after loading
            cutoff = datetime.now() - timedelta(days=30)
            self.data_log = [e for e in self.data_log if e[0] >= cutoff]
        except Exception as e:
            # If file is corrupt or missing, start fresh
            self.data_log = []

    # Restart

    def restart(self):
        self.initialised = False
        self.power_on = False
        self.mode = "auto"
        self.intensity = "auto"

        ok, msg = self.init_system()
        return ok, "System has been restarted. " + msg

    # Status text

    def status_text(self):
        """Compose a short single-line status summary for display in the UI.

        The returned string includes power, mode, intensity, sensor status,
        and recent telemetry values when available.
        """

        if not self.initialised:
            status = "Not initialized"
        elif not self.power_on:
            status = "Powered off"
        else:
            status = "Powered on"

        # Show OFF when not powered on
        if not self.power_on:
            mode = "OFF"
            intensity_display = "OFF"
        else:
            mode = "AUTO" if self.mode == "auto" else "MANUAL"
            intensity_display = "AUTO" if self.mode == "auto" else self.intensity.upper()

        # Arduino status (farver)
        if self.sensors_ok:
            arduino_status = "Arduino: \x1b[32mCONNECTED\x1b[0m"
        else:
            arduino_status = "Arduino: \x1b[31mNOT connected\x1b[0m"
        
        parts = [
            f"Power: {status}",
            f"Mode: {mode}",
            f"Intensity: {intensity_display}",
            arduino_status,
        ]

        # Add estimated LED brightness and power usage for quick glance
        try:
            # 5mm LED power in watts (20-100mW)
            mapping = {"low": 0.02, "medium": 0.06, "high": 0.1, "auto": 0.06}
            pct = {"low": 33, "medium": 66, "high": 100, "auto": 50}
            est_power = mapping.get(self.intensity, 0.0) if (self.initialised and self.power_on) else 0.0
            est_pct = pct.get(self.intensity, 0)
            if self.mode == "auto":
                parts.append(f"LED: AUTO (~{est_power*1000:.0f} mW avg)")
            else:
                parts.append(f"LED: {est_pct}% (~{est_power*1000:.0f} mW)")
        except Exception:
            pass
        if getattr(self, 'init_ok', False):
            parts.append('INIT: OK')
        if getattr(self, 'last_ambient', None) is not None:
            parts.append(f"Ambient: {self.last_ambient}")
        if getattr(self, 'last_motion', None) is not None:
            parts.append(f"Last motion: {self.last_motion}")

        # include any sensor_info entries
        for k, v in getattr(self, 'sensor_info', {}).items():
            parts.append(f"{k}: {v}")

        return ' | '.join(parts)

    # ---- Serial / background reader helpers ----
    def start_reader(self):
        """Start the background serial reader thread.

        The thread attempts to keep the UART connection open and will parse
        incoming lines into controller fields and append raw lines to
        `recent_raw` for the UI to consume.
        """

        if self.reader_thread and self.reader_thread.is_alive():
            return

        self.reader_stop.clear()
        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.reader_thread.start()

    def stop_reader(self):
        """Stop the background reader thread and close any open UART.

        Attempts to join the reader thread and stop the data sampler. It
        is safe to call multiple times.
        """

        if not self.reader_thread:
            return
        self.reader_stop.set()
        self.reader_thread.join(timeout=1)
        self.reader_thread = None
        # stop data sampler as well
        try:
            self.stop_data()
        except Exception:
            pass
        if self.uart:
            try:
                self.uart.close()
            except Exception:
                pass
            self.uart = None

    def _reader_loop(self):
        """Background loop that reads from UART and updates controller state.

        Behavior:
        - If the UART is disconnected, attempts to reconnect periodically.
        - Reads lines and pushes them to `reader_queue` and `recent_raw`.
        - Parses known message prefixes (INIT, SENSOR, Ambient, Motion)
          to update `init_ok`, `sensor_info`, `last_ambient`, and
          `last_motion` accordingly.
        """
        while not self.reader_stop.is_set():
            if self.uart is None or not self.uart.is_open():
                # try reconnect
                try:
                    old_ok = self.sensors_ok
                    self.uart = UARTReader(port=ARDUINO_PORT, baud=BAUD, timeout=0.5)
                    if self.uart.is_open():
                        self.sensors_ok = True
                        if not old_ok:
                            print(f"[Arduino] connected to {ARDUINO_PORT}")
                    else:
                        raise Exception("port not open")
                except Exception:
                    # kun hvis vi LIGE ER gået ned
                    if self.sensors_ok:
                        print(
                            "No connection to Arduino.\n"
                            "Go to Settings > Serial settings > Set COM port (type)\n"
                            "and choose the correct port."
                        )
                        # Add disconnect alert
                        now = datetime.now()
                        if self.last_disconnect_alert is None or (now - self.last_disconnect_alert).total_seconds() > 60:
                            self.alerts.append(("WARNING", now, "Arduino disconnected"))
                            self.last_disconnect_alert = now
                    self.sensors_ok = False
                    time.sleep(2)
                    continue

            try:
                line = self.uart.read_line()
                if line:
                    # Push to queue and print
                    try:
                        self.reader_queue.put_nowait(line)
                    except Exception:
                        pass
                    # store raw message (strip newline) so UI can show it persistently
                    try:
                        self.recent_raw.append(line.rstrip('\n'))
                    except Exception:
                        pass
                    
                    # Categorize messages for display
                    l = line.strip()
                    timestamp = time.strftime('%H:%M:%S')
                    try:
                        if 'motion detected' in l.lower():
                            self.recent_telemetry.append((timestamp, l))
                        elif 'light sensor' in l.lower() or 'lux' in l.lower():
                            self.recent_sensors.append((timestamp, l))
                        elif 'recalibrated' in l.lower() or ('brightness' in l.lower() and 'led' in l.lower()):
                            self.recent_other.append((timestamp, l))
                    except Exception:
                        pass
                    
                    # Parse some known message patterns to update GUI state
                    # INIT
                    if l.upper().startswith('INIT'):
                        self.init_ok = True
                    # SENSOR lines
                    elif l.upper().startswith('SENSOR LDR'):
                        # e.g. 'SENSOR LDR OK: 512' or 'SENSOR LDR FAIL'
                        self.sensor_info['LDR'] = l.partition(':')[2].strip() if ':' in l else l
                    elif l.upper().startswith('SENSOR PIR') or 'PIR' in l.upper():
                        # e.g. 'SENSOR PIR STATE: HIGH'
                        self.sensor_info['PIR'] = l.partition(':')[2].strip() if ':' in l else l
                    # Ambient / Lux lines
                    elif l.lower().startswith('ambient') or l.lower().startswith('lux'):
                        # try to extract number
                        import re
                        m = re.search(r"(\d{1,4})", l)
                        if m:
                            self.last_ambient = int(m.group(1))
                    elif 'motion detected' in l.lower():
                        self.last_motion = time.strftime('%H:%M:%S')

                    # Do not print raw lines here — UI shows recent messages in header.
                    # This avoids duplicate output flooding the console.
                    pass
                else:
                    # no data — short sleep to avoid tight loop
                    time.sleep(0.1)
            except Exception:
                # On error, drop uart and attempt reconnect
                try:
                    if self.uart:
                        self.uart.close()
                except Exception:
                    pass
                self.uart = None
                self.sensors_ok = False
                time.sleep(1)

    def set_port(self, port, baud=BAUD):
        """Change the serial port used by the controller.

        Args:
            port (str): Port name (e.g. 'COM9').
            baud (int): Baud rate (defaults to `BAUD`).

        This method stops the reader thread, attempts to open the new
        port and restarts the reader so that incoming messages are
        consumed from the new connection.
        """
        global ARDUINO_PORT, BAUD
        ARDUINO_PORT = port
        BAUD = baud
        # restart reader to pick up new port
        self.stop_reader()
        try:
            self.uart = UARTReader(port=ARDUINO_PORT, baud=BAUD, timeout=0.5)
            self.sensors_ok = True
        except Exception as e:
            print(f"Could not open port {port}: {e}")
            self.uart = None
            self.sensors_ok = False
        self.start_reader()

    # No logger: removed _log method — status shown via status_text()


# ----------------- GUI APP -----------------

class LSSApp:
    """Terminal-based application wrapper for the LSS controller.

    This class provides the interactive menus, header rendering,
    and keyboard navigation handling. It is intentionally minimal and
    uses `LSSController` as the model for system state.
    """

    def __init__(self, open_serial: bool = True):
        """Create the application and instantiate the controller model.

        Args:
            open_serial (bool): If False, the controller will not attempt to
                open a real serial port. Useful for offline testing with
                `FakeUART`.
        """
        self.ctrl = LSSController(open_serial=open_serial)

    # Terminal helpers

    def clear(self):
        if platform.system() == "Windows":
            os.system("cls")
        else:
            os.system("clear")

    def header(self, title):
        """Render the top header area including title and (optionally) status.

        The header prints a brief title, an optional section title, and the
        system status summary (if `self.ctrl.show_status` is True). It also
        shows small categorized recent-message snippets for quick inspection.
        """
        print("GUI - LSS Lighting Control System")
        if title:
            print(title)
        print("+--------------------------------------+\n")

        # Show/hide status bar with spacebar
        if getattr(self.ctrl, 'show_status', True):
            print(self.ctrl.status_text())
            print("-------------------------------------------------------------------------------------------------------------")
        
        # Show recent alerts (last 3)
        if self.ctrl.alerts:
            recent_alerts = self.ctrl.alerts[-3:]
            for alert_type, alert_time, alert_msg in recent_alerts:
                time_str = alert_time.strftime("%H:%M:%S")
                if alert_type == "WARNING":
                    print(f"⚠ [{time_str}] {alert_msg}")
                elif alert_type == "SUCCESS":
                    print(f"✓ [{time_str}] {alert_msg}")
            print()
        
        print()

    # Key reading (arrow keys)

    def read_key(self):
        """
        Returns one of: 'UP', 'DOWN', 'LEFT', 'RIGHT', 'OTHER'
        Cross-platform (Windows + Linux/mac).
        """
        if platform.system() != "Windows":
            # This GUI is Windows-focused. For non-Windows platforms
            # the input handling is not implemented. Raise a clear error
            # so the developer knows to add a cross-platform branch.
            raise RuntimeError("read_key() only implemented on Windows (msvcrt)")

        import msvcrt
        while True:
            ch = msvcrt.getch()
            # Ctrl-C
            if ch == b"\x03":
                raise KeyboardInterrupt()
            # special keys (arrow keys)
            if ch == b"\xe0":
                ch2 = msvcrt.getch()
                if ch2 == b"H":
                    return "UP"
                elif ch2 == b"P":
                    return "DOWN"
                elif ch2 == b"K":
                    return "LEFT"
                elif ch2 == b"M":
                    return "RIGHT"
            else:
                # allow 'q' to quit and detect SPACE
                try:
                    c = ch.decode()
                    if c == 'q' or c == 'Q':
                        raise KeyboardInterrupt()
                    if c == ' ':
                        return 'SPACE'
                except Exception:
                    pass
                return "OTHER"

    def read_key_timeout(self, timeout: float):
        """Non-blocking key read with timeout (seconds). Returns key string or None.

        Uses platform-specific polling: on Windows uses msvcrt.kbhit(), on Unix uses select.
        """
        if platform.system() != "Windows":
            raise RuntimeError("read_key_timeout() only implemented on Windows (msvcrt)")

        end = time.time() + timeout
        import msvcrt
        while time.time() < end:
            if msvcrt.kbhit():
                try:
                    ch = msvcrt.getch()
                    # handle special/arrow keys
                    if ch == b"\x03":
                        raise KeyboardInterrupt()
                    # Do not treat Enter/Return as RIGHT here; only right-arrow is Enter
                    if ch == b"\xe0":
                        ch2 = msvcrt.getch()
                        if ch2 == b"H":
                            return "UP"
                        if ch2 == b"P":
                            return "DOWN"
                        if ch2 == b"K":
                            return "LEFT"
                        if ch2 == b"M":
                            return "RIGHT"
                    else:
                        try:
                            c = ch.decode()
                            if c in ('q', 'Q'):
                                raise KeyboardInterrupt()
                            if c == ' ':
                                return 'SPACE'
                        except Exception:
                            pass
                        return 'OTHER'
                except KeyboardInterrupt:
                    raise
            time.sleep(0.02)
        return None

    def wait_key(self):
        print("\n(Press LEFT ARROW to go back)")
        while True:
            key = self.read_key()
            if key == "LEFT":
                return

    # Menu with arrow keys

    def menu_select(self, title, options, allow_back=False):
        """
        UP/DOWN arrows  = Navigate
        RIGHT arrow     = select / open (returns index)
        LEFT arrow      = back (returns -1, if allow_back=True)
        """
        index = 0
        first_render = True
        last_messages = []

        def render_full():
            # full clear+draw (for navigation updates)
            self.clear()
            self.header(title)
            # Menu options
            for i, label in enumerate(options):
                marker = "-->" if i == index else "  "
                print(f"{marker} {label}")

            # lidt luft og så "bund-bar" under menuen
            print("\n")

            # Vis Arduino-status over navigations-hints, men kun hvis ikke forbundet
            if not getattr(self.ctrl, "sensors_ok", False):
                print("\x1b[31mArduino: NOT connected\x1b[0m")

            print("\n[↑/↓] Navigate    [→] Enter    [←] Back    [SPACE] Toggle Details")
            print("------------------------------------------------------------------------------------------------------------- ")
            
            # Show categorized messages with timestamps (toggle with SPACE)
            if getattr(self.ctrl, 'show_status', False):
                try:
                    telemetry = list(self.ctrl.recent_telemetry)
                    sensors = list(self.ctrl.recent_sensors)
                    other = list(self.ctrl.recent_other)
                    
                    print('\nEvents:')
                    if telemetry:
                        for ts, ln in telemetry:
                            ln2 = ln.replace('\n', ' ').rstrip()
                            print(f'  [{ts}] ' + _colorize_tokens(ln2))
                    
                    print('\nSensors:')
                    if sensors:
                        for ts, ln in sensors:
                            ln2 = ln.replace('\n', ' ').rstrip()
                            print(f'  [{ts}] {ln2}')
                    
                    print('\nOther:')
                    if other:
                        for ts, ln in other:
                            ln2 = ln.replace('\n', ' ').rstrip()
                            print(f'  [{ts}] {ln2}')
                except Exception:
                    pass
            # Status update will be handled on next menu render
            pass

        while True:
            if first_render:
                render_full()
                last_messages = list(self.ctrl.recent_raw)
                first_render = False

            # Footer removed - recent messages now in status bar (toggle with SPACE)

            # poll for a key with timeout so UI refreshes live
            key = None
            try:
                key = self.read_key_timeout(1.0)
            except KeyboardInterrupt:
                raise
            except Exception:
                key = None

            if key is None:
                # Auto-update when new messages arrive (only re-renders if messages changed)
                current_messages = list(self.ctrl.recent_raw)
                if current_messages != last_messages:
                    render_full()
                    last_messages = current_messages
                continue

            if key == "UP":
                index = (index - 1) % len(options)
                render_full()
                continue
            elif key == "DOWN":
                index = (index + 1) % len(options)
                render_full()
                continue
            elif key == "SPACE":
                # Toggle show/hide of the entire status/header
                try:
                    self.ctrl.show_status = not self.ctrl.show_status
                except Exception:
                    pass
                render_full()
                continue
            elif key == "LEFT" and allow_back:
                return -1
            elif key == "RIGHT":
                return index
            # OTHER ignored; continue waiting
    def _read_numeric_input_with_cancel(self, max_len: int = 4) -> str | None:
        """Read numeric input char-by-char using msvcrt and allow LEFT arrow to cancel.

        Returns the numeric string entered, or None if canceled with LEFT arrow.
        Echoes typed characters to stdout and handles backspace.
        """
        if platform.system() != 'Windows':
            # Fallback to blocking input on non-Windows
            try:
                s = input().strip()
                return s if s.isdigit() else None
            except Exception:
                return None

        import msvcrt
        buf = []
        sys.stdout.write('')
        sys.stdout.flush()
        while True:
            if not msvcrt.kbhit():
                time.sleep(0.02)
                continue
            ch = msvcrt.getch()
            # Ctrl-C
            if ch == b'\x03':
                raise KeyboardInterrupt()
            # Enter/Return -> finish
            # Enter/Return should NOT finish input here; only RIGHT-arrow does.
            if ch in (b'\r', b'\n'):
                # ignore Enter
                continue
            # Special key (arrow/backspace): 0x00 or 0xe0 prefix
            if ch in (b'\x00', b'\xe0'):
                ch2 = msvcrt.getch()
                # LEFT arrow -> cancel
                if ch2 == b'K':
                    return None
                # RIGHT arrow -> finish input (acts as ENTER)
                if ch2 == b'M':
                    s = ''.join(buf).strip()
                    return s if s.isdigit() else None
                # ignore other arrows
                continue
            # Backspace
            if ch == b'\x08':
                if buf:
                    buf.pop()
                    # erase last char from console
                    sys.stdout.write('\b \b')
                    sys.stdout.flush()
                continue
            # Printable chars
            try:
                c = ch.decode()
            except Exception:
                continue
            if c.isdigit() and len(buf) < max_len:
                buf.append(c)
                sys.stdout.write(c)
                sys.stdout.flush()
            # ignore other chars

    # Main menu

    def main_menu(self):
        options = [
            "Power on / off",
            "Settings >",
            "Data >",
            "System Info >",
            "Help >",
        ]
        choice = self.menu_select("", options, allow_back=False)

        if choice == 0:
            self.menu_power()
        elif choice == 1:
            self.menu_settings()
        elif choice == 2:
            self.menu_data()
        elif choice == 3:
            self.menu_system_info()
        elif choice == 4:
            self.menu_help()

    # Power

    def menu_power(self):
        self.clear()
        self.header("Power on / off")
        ok, msg = self.ctrl.toggle_power()
        print(msg)
        # Send til Arduino:
        if self.ctrl.power_on:
            self.ctrl.send_arduino("POWER ON")
        else:
            self.ctrl.send_arduino("POWER OFF")
        if not ok:
            print("\n[ERROR]")
        self.wait_key()

    # Settings

    def menu_settings(self):
        while True:
            options = [
                "Manual >",
                "Automatic >",
                "Change COM port >",
                "Restart System >",
            ]
            choice = self.menu_select("Settings", options, allow_back=True)

            if choice == -1:
                break
            elif choice == 0:
                self.menu_manual()
            elif choice == 1:
                self.menu_automatic()
            elif choice == 2:
                self.menu_change_comport()
            elif choice == 3:
                self.menu_restart()

    def menu_restart(self):
        """Restart the LSS system."""
        self.clear()
        self.header("Restart System")
        
        print("This will restart the LSS system.")
        print("Current state will be lost.")
        print()
        
        # Confirmation menu
        options = ["Yes", "No (Cancel)"]
        idx = 0
        
        while True:
            self.clear()
            self.header("Restart System - Confirmation")
            print()
            print("⚠️  This will restart the LSS system.")
            print("    Current state will be lost.")
            print()
            print("Are you sure?")
            print()
            
            for i, opt in enumerate(options):
                if i == idx:
                    print(f"  > {opt}")
                else:
                    print(f"    {opt}")
            
            print()
            print("Use ↑/↓ to select, → to confirm, ← to cancel")
            
            key = self.read_key()
            if key == "UP" and idx > 0:
                idx -= 1
            elif key == "DOWN" and idx < len(options) - 1:
                idx += 1
            elif key == "RIGHT":
                break
            elif key == "LEFT":
                idx = 1  # Cancel
                break
        
        self.clear()
        self.header("Restart System")
        print()
        
        if idx == 0:  # Yes
            print("Restarting Arduino...")
            print()
            
            # Power off
            try:
                self.ctrl.send_arduino("POWER OFF")
                time.sleep(0.5)
                print("✓ Arduino powered off")
            except Exception as e:
                print(f"✗ Could not communicate with Arduino: {e}")
                print()
                self.wait_key()
                return
            
            # Reset local controller state
            self.ctrl.power_on = False
            self.ctrl.mode = "auto"
            self.ctrl.intensity = "medium"
            self.ctrl.initialised = False
            self.ctrl.power_on_time = None
            
            # Power on
            time.sleep(0.5)
            try:
                self.ctrl.send_arduino("POWER ON")
                time.sleep(0.5)
                print("✓ Arduino powered on")
            except Exception as e:
                print(f"✗ Could not communicate with Arduino: {e}")
                print()
                self.wait_key()
                return
            
            # Update local state
            self.ctrl.power_on = True
            self.ctrl.initialised = True
            self.ctrl.power_on_time = datetime.now()
            
            print()
            print("✓ Restart completed successfully!")
            print("System is now running in AUTO mode.")
        else:
            print("Restart cancelled.")
        
        print()
        self.wait_key()

    def menu_change_comport(self):
        """Change COM port setting."""
        self.clear()
        self.header("Change COM port")
        print("Enter COM port number (e.g. 9 for COM9), LEFT arrow cancels while typing:")
        # Read numeric input with ability to cancel using LEFT arrow
        portnum = self._read_numeric_input_with_cancel()
        if portnum is None:
            print("Cancelled.")
            self.wait_key()
            return
        port = f"COM{int(portnum)}"
        try:
            self.ctrl.set_port(port, BAUD)
            print(f"✓ Port changed to {port}")
        except Exception as e:
            print(f"✗ Could not set port: {e}")
        self.wait_key()

    def menu_manual(self):
        while True:
            options = [
                "Low",
                "Medium",
                "High",
            ]
            choice = self.menu_select("Manual – choose light intensity", options, allow_back=True)

            if choice == -1:
                return

            mapping = {0: "low", 1: "medium", 2: "high"}
            level = mapping[choice]
            self.clear()
            self.header("Manual – choose light intensity")
            ok, msg = self.ctrl.set_manual_intensity(level)
            print(msg)
            if ok:
                if level == "low":
                    self.ctrl.send_arduino("SET INTENSITY LOW")
                elif level == "medium":
                    self.ctrl.send_arduino("SET INTENSITY MEDIUM")
                elif level == "high":
                    self.ctrl.send_arduino("SET INTENSITY HIGH")
            self.wait_key()
            return

    def menu_automatic(self):
        options = [
            "Yes",
            "No",
        ]
        choice = self.menu_select("Automatic", options, allow_back=True)

        if choice == -1:
            return

        self.clear()
        self.header("Automatic")
        if choice == 1:
            print("Cancelled.")
            self.wait_key()
            return

        ok, msg = self.ctrl.set_automatic()
        print(msg)
        if ok:
            self.ctrl.send_arduino("AUTO")
        self.wait_key()

    # Data

    def menu_data(self):
        while True:
            options = [
                "View Monitoring Data",
                "View Power Graphs",
                "Clear old data (keep last 7 days)",
            ]
            choice = self.menu_select("Data", options, allow_back=True)
            
            if choice == -1:
                break
            elif choice == 0:
                self.submenu_view_data()
            elif choice == 1:
                self.submenu_view_graphs()
            elif choice == 2:
                self.clear_old_data()

    def submenu_view_data(self):
        def _render_data():
            self.clear()
            self.header("Data – Monitoring")
            data = self.ctrl.get_data()
            stats = self.ctrl.get_data_stats()

            if not data:
                print("There is no data to display.")
                return

            # Current brightness mode
            brightness_mode = self.ctrl.intensity.upper() if self.ctrl.power_on else "OFF"
            print(f"Current brightness: {brightness_mode}")
            print(f"Current LED power: {stats['current_power']*1000:.1f} mW ({stats['current_power']:.4f} W)")
            print(f"\nLast hour average: {stats['hourly_avg']*1000:.1f} mW")
            print(f"Last 24h — min: {stats['day_min']*1000:.1f} mW  max: {stats['day_max']*1000:.1f} mW  avg: {stats['day_avg']*1000:.1f} mW")
            print(f"Total energy (last 24h): {stats['total_wh']:.4f} Wh ({stats['total_wh']/1000:.6f} kWh)")
            
            # Calculate savings compared to always-on at 100%
            max_power_wh = 0.1 * 24  # 100mW for 24 hours = 2.4 Wh
            savings_wh = max_power_wh - stats['total_wh']
            savings_kwh = savings_wh / 1000
            savings_dkk = savings_kwh * 1.8  # Danish electricity price
            print(f"\nEnergy savings (last 24h):")
            print(f"  Compared to 100% brightness: {savings_wh:.4f} Wh ({savings_kwh:.6f} kWh)")
            print(f"  Cost savings: {savings_dkk:.4f} DKK (@ 1.8 DKK/kWh)")
            print('\nRecent samples (LED only, last 5):')
            print("Time                 | Power (mW) | Brightness")
            print("------------------------------------------------------")
            for ts, val in data[-5:]:
                # Calculate brightness percentage from power (20mW=LOW, 60mW=MED, 100mW=HIGH)
                brightness_pct = int((val / 0.1) * 100) if val > 0 else 0
                brightness_pct = min(100, brightness_pct)
                print(f"{ts} | {val*1000:>6.1f}     | {brightness_pct}%")

        # initial render
        _render_data()
        # Handle keys directly so SPACE can toggle header and cause a re-render
        while True:
            try:
                key = self.read_key()
            except KeyboardInterrupt:
                raise
            if key == 'LEFT':
                return
            if key == 'SPACE':
                try:
                    self.ctrl.show_status = not self.ctrl.show_status
                except Exception:
                    pass
                _render_data()
                continue
            # ignore other keys
            continue

    def submenu_view_graphs(self):
        """Display text-based bar graphs for power usage trends."""
        self.clear()
        self.header("Data – Power Graphs")
        
        data = self.ctrl.get_data()
        if not data:
            print("No data available for graphs.")
            self.wait_key()
            return
        
        # Get last 24 hours of data
        now = datetime.now()
        cutoff = now - timedelta(hours=24)
        
        recent_data = []
        for ts_str, val in data:
            try:
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                if ts >= cutoff:
                    recent_data.append((ts, val))
            except Exception:
                continue
        
        if not recent_data:
            print("No data in last 24 hours.")
            self.wait_key()
            return
        
        # Group by hour and calculate averages
        hourly_data = {}
        for ts, val in recent_data:
            hour_key = ts.replace(minute=0, second=0, microsecond=0)
            if hour_key not in hourly_data:
                hourly_data[hour_key] = []
            hourly_data[hour_key].append(val)
        
        # Calculate averages
        hourly_avg = {hour: sum(vals) / len(vals) for hour, vals in hourly_data.items()}
        
        # Sort by time
        sorted_hours = sorted(hourly_avg.keys())
        
        # Find max for scaling
        max_power = max(hourly_avg.values()) if hourly_avg else 0.1
        
        print("Power Usage - Last 24 Hours (hourly averages)")
        print("=" * 60)
        print()
        
        # Display bar graph (up to last 24 hours)
        display_hours = sorted_hours[-24:]  # Last 24 hours max
        
        for hour in display_hours:
            avg_power = hourly_avg[hour]
            power_mw = avg_power * 1000
            
            # Calculate bar length (max 40 chars)
            bar_length = int((avg_power / max_power) * 40)
            bar = "█" * bar_length
            
            hour_str = hour.strftime("%H:%M")
            print(f"{hour_str} | {bar} {power_mw:6.1f} mW")
        
        print()
        print(f"Scale: Max = {max_power*1000:.1f} mW")
        print()
        print("Press any key to return...")
        
        self.wait_key()

    def clear_old_data(self):
        """Clear data older than 7 days."""
        self.clear()
        self.header("Clear Old Data")
        
        data = self.ctrl.get_data()
        if not data:
            print("No data to clear.")
            self.wait_key()
            return
        
        cutoff = datetime.now() - timedelta(days=7)
        original_count = len(data)
        
        # Filter data to keep only last 7 days
        new_data = [(ts, val) for ts, val in data 
                    if datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") > cutoff]
        
        removed = original_count - len(new_data)
        
        if removed == 0:
            print("No data older than 7 days found.")
            self.wait_key()
            return
        
        print(f"This will remove {removed} samples older than 7 days.")
        print(f"Keeping {len(new_data)} recent samples.")
        print("\nAre you sure? (y/n): ", end='', flush=True)
        
        confirm = input().strip().lower()
        if confirm == 'y':
            # Update controller's data log
            self.ctrl.data_log = new_data
            self.ctrl._save_data_log()
            print(f"\nSuccessfully removed {removed} old samples.")
        else:
            print("\nCancelled.")
        
        self.wait_key()

    # System Info

    def menu_system_info(self):
        """Display system information including versions, uptime, and statistics."""
        self.clear()
        self.header("System Information")
        
        # ===== SOFTWARE INFO =====
        print("=" * 60)
        print("SOFTWARE INFORMATION")
        print("=" * 60)
        print(f"GUI Version:           {self.ctrl.gui_version}")
        print(f"Python Version:        {sys.version.split()[0]}")
        print(f"Platform:              {platform.system()} {platform.release()}")
        print(f"Architecture:          {platform.machine()}")
        print()
        
        # ===== SYSTEM SPECS =====
        print("=" * 60)
        print("COMPUTER SPECIFICATIONS")
        print("=" * 60)
        print(f"Computer Name:         {platform.node()}")
        print(f"Processor:             {platform.processor()}")
        try:
            import psutil
            print(f"CPU Cores:             {psutil.cpu_count(logical=False)} physical, {psutil.cpu_count(logical=True)} logical")
            print(f"CPU Usage:             {psutil.cpu_percent(interval=0.1)}%")
            mem = psutil.virtual_memory()
            print(f"RAM Total:             {mem.total / (1024**3):.1f} GB")
            print(f"RAM Available:         {mem.available / (1024**3):.1f} GB ({mem.percent}% used)")
            disk = psutil.disk_usage('/')
            print(f"Disk Total:            {disk.total / (1024**3):.1f} GB")
            print(f"Disk Free:             {disk.free / (1024**3):.1f} GB ({disk.percent}% used)")
        except ImportError:
            print("(Install 'psutil' for detailed system info)")
        print()
        
        # ===== LSS SYSTEM STATUS =====
        print("=" * 60)
        print("LSS SYSTEM STATUS")
        print("=" * 60)
        print(f"Power Status:          {'ON' if self.ctrl.power_on else 'OFF'}")
        print(f"Operating Mode:        {self.ctrl.mode.upper()}")
        print(f"LED Intensity:         {self.ctrl.intensity.upper()}")
        
        # Uptime
        if self.ctrl.power_on_time:
            uptime = datetime.now() - self.ctrl.power_on_time
            hours = int(uptime.total_seconds() // 3600)
            minutes = int((uptime.total_seconds() % 3600) // 60)
            seconds = int(uptime.total_seconds() % 60)
            print(f"System Uptime:         {hours}h {minutes}m {seconds}s")
            print(f"Powered On Since:      {self.ctrl.power_on_time.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            print("System Uptime:         Not powered on")
        print()
        
        # ===== ARDUINO CONNECTION =====
        print("=" * 60)
        print("ARDUINO INFORMATION")
        print("=" * 60)
        if self.ctrl.uart and self.ctrl.uart.ser and self.ctrl.uart.ser.is_open:
            print(f"Status:                CONNECTED")
            print(f"Serial Port:           {self.ctrl.uart.ser.port}")
            print(f"Baud Rate:             {self.ctrl.uart.ser.baudrate} baud")
            print(f"Timeout:               {self.ctrl.uart.ser.timeout}s")
            try:
                # Get serial port info
                import serial.tools.list_ports
                ports = serial.tools.list_ports.comports()
                for p in ports:
                    if p.device == self.ctrl.uart.ser.port:
                        print(f"Device:                {p.description}")
                        print(f"Hardware ID:           {p.hwid}")
                        if hasattr(p, 'manufacturer') and p.manufacturer:
                            print(f"Manufacturer:          {p.manufacturer}")
                        break
            except Exception:
                pass
            print(f"Sensors OK:            {'YES' if self.ctrl.sensors_ok else 'NO'}")
        else:
            print(f"Status:                DISCONNECTED")
            print(f"Sensors OK:            NO")
        print()
        
        # ===== DATA LOGGING INFO =====
        print("=" * 60)
        print("DATA LOGGING")
        print("=" * 60)
        try:
            data = self.ctrl.get_data()
            print(f"Total Data Points:     {len(data)}")
            if data:
                print(f"Oldest Sample:         {data[0][0]}")
                print(f"Newest Sample:         {data[-1][0]}")
                
                # Calculate data file size
                import os
                if os.path.exists(self.ctrl.data_log_file):
                    file_size = os.path.getsize(self.ctrl.data_log_file)
                    print(f"CSV File Size:         {file_size / 1024:.2f} KB")
                    print(f"CSV File Location:     {os.path.abspath(self.ctrl.data_log_file)}")
            else:
                print("No data collected yet")
        except Exception as e:
            print(f"Total Data Points:     0 (Error: {e})")
        print()
        
        # ===== THREAD STATUS =====
        print("=" * 60)
        print("BACKGROUND THREADS")
        print("=" * 60)
        print(f"Serial Reader:         {'RUNNING' if self.ctrl.reader_thread and self.ctrl.reader_thread.is_alive() else 'STOPPED'}")
        print(f"Data Logger:           {'RUNNING' if self.ctrl.data_thread and self.ctrl.data_thread.is_alive() else 'STOPPED'}")
        print()
        
        # ===== MEMORY/PERFORMANCE =====
        print("=" * 60)
        print("PERFORMANCE METRICS")
        print("=" * 60)
        try:
            import psutil
            process = psutil.Process()
            print(f"GUI Memory Usage:      {process.memory_info().rss / (1024**2):.1f} MB")
            print(f"GUI CPU Usage:         {process.cpu_percent(interval=0.1):.1f}%")
            print(f"Open Files:            {len(process.open_files())}")
            print(f"Thread Count:          {process.num_threads()}")
        except ImportError:
            print("(Install 'psutil' for performance metrics)")
        
        print("=" * 60)
        self.wait_key()

    # Help

    def menu_help(self):
        while True:
            options = [
                "What is LSS? >",
                "How It Works >",
                "Manual vs Automatic >",
                "Understanding Data >",
                "Keyboard Controls >",
                "Troubleshooting >",
            ]
            choice = self.menu_select("Help", options, allow_back=True)

            if choice == -1:
                break
            elif choice == 0:
                self.help_about()
            elif choice == 1:
                self.help_how_it_works()
            elif choice == 2:
                self.help_modes()
            elif choice == 3:
                self.help_data()
            elif choice == 4:
                self.help_keyboard()
            elif choice == 5:
                self.help_reset()

    def help_about(self):
        self.clear()
        self.header("What is LSS?")
        print("LSS (Light Sensing System) is an intelligent LED lighting controller")
        print("that combines Arduino firmware with a Python GUI interface.")
        print()
        print("KEY FEATURES:")
        print("• Automatic brightness adjustment based on ambient light")
        print("• Motion-aware recalibration (faster response when motion detected)")
        print("• Manual control modes (LOW/MEDIUM/HIGH)")
        print("• Real-time power monitoring (LED: 20-100mW)")
        print("• Energy savings tracking with cost analysis (DKK)")
        print("• Persistent data logging (30-day retention)")
        print("• Export data to CSV or JSON formats")
        print("• Visual power usage graphs")
        print()
        print("HARDWARE:")
        print("• Arduino Mega 2560 microcontroller")
        print("• LDR (Light Dependent Resistor) sensor - measures ambient light")
        print("• PIR (Passive Infrared) sensor - detects motion")
        print("• 5mm LED output (PWM-controlled brightness)")
        self.wait_key()

    def help_how_it_works(self):
        self.clear()
        self.header("How It Works")
        print("SENSOR SYSTEM:")
        print("• LDR Sensor reads ambient light levels (375-975 lux range)")
        print("• PIR Sensor detects motion in the room")
        print("• Arduino processes sensor data and controls LED brightness")
        print()
        print("AUTOMATIC BRIGHTNESS CONTROL:")
        print("• Brighter ambient light → Dimmer LED (saves energy)")
        print("• Darker ambient light → Brighter LED (maintains visibility)")
        print("• PWM (Pulse Width Modulation) controls LED brightness smoothly")
        print()
        print("MOTION DETECTION:")
        print("• When motion is detected, system recalibrates faster (every 2 seconds)")
        print("• Without motion, recalibration slows down (every 5 seconds)")
        print("• This optimizes responsiveness while saving processing power")
        print()
        print("DATA LOGGING:")
        print("• Power consumption is sampled every 10 seconds")
        print("• Data is saved to 'lss_power_log.csv' for persistence")
        print("• Old data (>30 days) is automatically removed")
        print()
        print("COMMUNICATION:")
        print("• Arduino and Python GUI communicate via USB serial (9600 baud)")
        print("• Commands are sent from GUI to Arduino")
        print("• Sensor readings and status updates are sent back to GUI")
        self.wait_key()

    def help_modes(self):
        self.clear()
        self.header("Manual vs Automatic")
        print("AUTOMATIC MODE (Default):")
        print("• System automatically adjusts LED brightness")
        print("• Responds to ambient light changes:")
        print("  - Bright room (975 lux) → LED at minimum (saves energy)")
        print("  - Dark room (375 lux) → LED at maximum (maintains visibility)")
        print("• Recalibrates every 2-5 seconds (motion-dependent)")
        print("• Enabled by default when you power on the system")
        print("• Best for energy savings and hands-free operation")
        print()
        print("MANUAL MODE:")
        print("• You manually select brightness level:")
        print("  - LOW: 20 mW (~20% brightness)")
        print("  - MEDIUM: 60 mW (~60% brightness)")
        print("  - HIGH: 100 mW (100% brightness)")
        print("• Brightness stays fixed until you change it")
        print("• Use when you want consistent lighting regardless of ambient light")
        print("• Brightness Lock feature prevents accidental changes")
        print()
        print("SWITCHING MODES:")
        print("• Settings → Manual (choose LOW/MEDIUM/HIGH)")
        print("• Settings → Automatic (enable auto mode)")
        print("• Switching to Manual disables automatic adjustments")
        print("• Switching to Automatic re-enables sensor-based control")
        print()
        print("ENERGY COMPARISON:")
        print("• Auto mode typically uses 40-70 mW average (saves ~30-60% energy)")
        print("• Manual HIGH uses 100 mW constantly")
        print("• Savings shown in Data menu (DKK based on 1.8 DKK/kWh)")
        self.wait_key()

    def help_data(self):
        self.clear()
        self.header("Understanding Data")
        print("POWER CONSUMPTION:")
        print("• Measured in milliwatts (mW) or watts (W)")
        print("• 1000 mW = 1 W")
        print("• LED power range: 20-100 mW (0.02-0.1 W)")
        print()
        print("DATA COLLECTION:")
        print("• System samples power every 10 seconds")
        print("• Data is saved to 'lss_power_log.csv'")
        print("• Format: timestamp, power_watts")
        print("• Retained for 30 days, then auto-deleted")
        print()
        print("STATISTICS SHOWN:")
        print("• Current Power: Instantaneous LED power consumption")
        print("• Hourly Average: Mean power over last 60 minutes")
        print("• Daily Min/Max/Avg: Statistics for last 24 hours")
        print("• Total Energy: Cumulative energy in Wh (Watt-hours)")
        print()
        print("ENERGY SAVINGS:")
        print("• Baseline: 100 mW LED running 24 hours = 2.4 Wh")
        print("• Savings = Baseline - Your actual usage")
        print("• Cost savings in DKK (Danish Kroner):")
        print("  Formula: (Energy saved in kWh) × 1.8 DKK/kWh")
        print()
        print("POWER GRAPHS:")
        print("• Visual bar charts showing hourly averages")
        print("• Last 24 hours of data")
        print("• Bar length represents power level")
        print("• Helps identify usage patterns")
        print()
        print("EXPORT OPTIONS:")
        print("• CSV: Raw data for spreadsheet analysis")
        print("• JSON: Structured data with metadata")
        print("• Both formats preserve timestamps and power values")
        self.wait_key()

    def help_keyboard(self):
        self.clear()
        self.header("Keyboard Controls")
        print("NAVIGATION:")
        print("• ↑ (Up Arrow): Move up in menus")
        print("• ↓ (Down Arrow): Move down in menus")
        print("• ENTER: Select highlighted option")
        print("• ← (Left Arrow): Go back to previous menu")
        print()
        print("DISPLAY CONTROLS:")
        print("• SPACE: Toggle status bar on/off")
        print("  - Shows/hides power status, mode, intensity")
        print("  - Shows/hides recent messages (Events/Sensors/Other)")
        print("  - Shows/hides alert notifications")
        print()
        print("EXIT CONTROLS:")
        print("• q (lowercase): Quit from main menu")
        print("• Ctrl+C: Force quit from anywhere (emergency exit)")
        print("• ESC: Alternative quit key from main menu")
        print()
        print("STATUS BAR CATEGORIES (when visible):")
        print("• Events: Motion detection, recalibration events")
        print("• Sensors: LDR readings, lux values, PIR states")
        print("• Other: LED brightness updates, system messages")
        print()
        print("TIPS:")
        print("• Use ← to quickly navigate back through menus")
        print("• Toggle status bar off (SPACE) if display updates are distracting")
        print("• Press any key to continue after viewing help screens")
        self.wait_key()

    def help_reset(self):
        self.clear()
        self.header("Troubleshooting")
        print("ARDUINO NOT DETECTED:")
        print("• Check USB cable is properly connected")
        print("• Verify Arduino appears in Windows Device Manager")
        print("• Try: Settings → Serial settings → Reconnect")
        print("• If auto-detection fails: Set COM port manually")
        print("  (Find COM number in Device Manager → Ports)")
        print()
        print("LED NOT RESPONDING:")
        print("• Make sure system is powered ON (check main menu)")
        print("• Verify you're in Auto mode or have set Manual intensity")
        print("• Check LED connection to Arduino pin 9")
        print("• Test with Manual mode HIGH to verify LED works")
        print()
        print("NO DATA DISPLAYED:")
        print("• System must be powered ON to collect data")
        print("• Data starts accumulating after power-on")
        print("• Wait 10+ seconds for first samples to appear")
        print("• Check 'lss_power_log.csv' file exists in GUI folder")
        print()
        print("GUI FREEZING / NOT UPDATING:")
        print("• Press SPACE to toggle status bar (reduces display load)")
        print("• Check serial connection is stable")
        print("• Close other programs using the COM port (Arduino IDE, etc.)")
        print("• Restart the GUI: Close and run 'python lss_gui.py' again")
        print()
        print("SERIAL PORT ACCESS DENIED:")
        print("• Close Arduino IDE Serial Monitor")
        print("• Close PlatformIO device monitor")
        print("• Only one program can use serial port at a time")
        print("• After closing conflicting program, use Reconnect option")
        print()
        print("BRIGHTNESS NOT CHANGING IN AUTO MODE:")
        print("• Cover/uncover LDR sensor to test")
        print("• Check sensor connections (LDR on pin A0)")
        print("• Wait 2-5 seconds for recalibration")
        print("• Motion detection affects update speed")
        print()
        print("MOTION NOT DETECTED:")
        print("• PIR sensor needs 30-60 seconds warm-up after power-on")
        print("• Check PIR sensor connection (pin A2)")
        print("• Verify PIR has power (5V)")
        print("• Move in front of sensor (detection range: ~7 meters)")
        print()
        print("STILL HAVING ISSUES?")
        print("• Check main README.md for detailed documentation")
        print("• Review USER_GUIDE.md for setup instructions")
        print("• Verify all hardware connections")
        print("• Re-upload Arduino firmware: 'platformio run --target upload'")
        self.wait_key()

    # Run loop

    def run(self):
        while True:
            self.main_menu()


# ----------------- MAIN -----------------

if __name__ == "__main__":
    print("Starting LSS... Use arrow keys to navigate, press 'q' or Ctrl+C to quit")
    app = LSSApp()
    try:
        app.run()
    except KeyboardInterrupt:
        print('\nReceived interrupt, shutting down...')
        # Power off the system
        try:
            if app.ctrl.power_on:
                app.ctrl.send_arduino("POWER OFF")
                app.ctrl.power_on = False
                print("System powered off")
        except Exception as e:
            print(f"Warning: Could not send power off command: {e}")
        # Stop threads
        try:
            app.ctrl.stop_reader()
        except Exception:
            pass
        try:
            app.ctrl.stop_data()
        except Exception:
            pass
        sys.exit(0)
