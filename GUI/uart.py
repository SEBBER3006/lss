import serial
import serial.tools.list_ports
from typing import Optional


def find_arduino_port() -> Optional[str]:
    """
    Scan alle serielle porte og prøv at finde en Arduino-lignende device.

    Returnerer f.eks. "COM5" hvis den findes, ellers None.
    """
    for p in serial.tools.list_ports.comports():
        desc = (p.description or "").lower()
        manu = (getattr(p, "manufacturer", "") or "").lower()
        hwid = (p.hwid or "").lower()

        # Udvid/tilpas disse checks hvis nødvendigt til jeres specifikke board
        if "arduino" in desc or "arduino" in manu or "usb serial device" in desc:
            return p.device  # typisk "COM5" på Windows

        # fallback: nogle boards viser kun USB VID/PID i hwid
        if "vid_2341" in hwid or "vid_2a03" in hwid:  # officielle Arduino VID'er
            return p.device

    return None


DEFAULT_BAUD = 9600


class UARTReader:
    """
    Simpel wrapper om pyserial til brug fra GUI'en.

    - Forsøger automatisk at finde Arduino-port, hvis port=None.
    - is_open() fortæller om serielporten er åben.
    - send_command() sender tekstlinjer.
    - read_line() læser én tekstlinje.
    """

    def __init__(self, port: Optional[str] = None, baud: int = DEFAULT_BAUD, timeout: float = 0.5):
        # Hvis ingen port er givet, prøv at autodetektere
        if port is None:
            port = find_arduino_port()

        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.ser: Optional[serial.Serial] = None

        if self.port is not None:
            try:
                self.ser = serial.Serial(self.port, self.baud, timeout=self.timeout)
            except Exception:
                self.ser = None

    def is_open(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def close(self) -> None:
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    def send_command(self, cmd: str) -> None:
        if not self.is_open():
            return
        assert self.ser is not None
        data = (cmd + "\n").encode("ascii", errors="ignore")
        self.ser.write(data)

    def read_line(self) -> Optional[str]:
        if not self.is_open():
            return None
        assert self.ser is not None
        line = self.ser.readline()
        if not line:
            return None
        return line.decode("ascii", errors="ignore").rstrip("\r\n")

    def read_value(self) -> Optional[float]:
        """Hvis Arduino sender rene tal-linjer, kan dette bruges; ellers kan du ignorere den."""
        txt = self.read_line()
        if txt is None:
            return None
        try:
            return float(txt.strip())
        except ValueError:
            return None
