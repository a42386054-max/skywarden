import sys
import os
import json
import re
import time
import math
import socket
import logging
import itertools
import functools
import requests
import ephem
from xml.sax.saxutils import quoteattr
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QComboBox, QPushButton, QMessageBox,
                             QTextEdit, QTabWidget, QFormLayout, QSpinBox,
                             QDoubleSpinBox, QCheckBox)
import concurrent.futures
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QIcon

CONFIG_FILE = "config_skywarden.json"
LOG_FILE = "skywarden_log.txt"

# --- CORE LOGGING CONFIGURATION ---
_log_handlers = [logging.FileHandler(LOG_FILE, encoding='utf-8')]
if sys.stdout is not None:   # a windowed (no-console) exe has no stdout
    _log_handlers.append(logging.StreamHandler(sys.stdout))
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=_log_handlers
)

THEMES = {
    "Deep Space Gray (Dark)": """
        QWidget { background-color: #1e1e24; color: #f4f4f9; font-family: 'Arial'; }
        QTabWidget::pane { border: 1px solid #3e3e42; }
        QTabBar::tab { background: #2d2d34; color: #b2b2b2; border: 1px solid #3e3e42; padding: 6px 12px; }
        QTabBar::tab:selected { background: #3e3e42; color: #ffffff; }
        QLineEdit, QComboBox, QTextEdit, QSpinBox, QDoubleSpinBox {
            background-color: #2d2d34; color: #ffffff; border: 1px solid #4b4b54; padding: 4px;
        }
        QPushButton { background-color: #007acc; color: #ffffff; border: none; border-radius: 4px; padding: 6px; }
        QPushButton:hover { background-color: #0098ff; }
    """,
    "Astro Red (Night Vision)": """
        QWidget { background-color: #120000; color: #ff0000; font-family: 'Arial'; }
        QTabWidget::pane { border: 1px solid #4a0000; }
        QTabBar::tab { background: #220000; color: #aa0000; border: 1px solid #4a0000; padding: 6px 12px; }
        QTabBar::tab:selected { background: #4a0000; color: #ff0000; }
        QLineEdit, QComboBox, QTextEdit, QSpinBox, QDoubleSpinBox {
            background-color: #220000; color: #ff0000; border: 1px solid #ff0000; padding: 4px;
        }
        QPushButton { background-color: #660000; color: #ff0000; border: 1px solid #ff0000; border-radius: 4px; padding: 6px; }
        QPushButton:hover { background-color: #880000; }
    """,
    "Standard Light Mode": ""
}

# Deep-sky catalogue entries (ephem.readdb format). Solar-system bodies are handled separately.
DEEP_SKY = {
    "M31": "M31,f|G,0:42:44.3,+41:16:09,3.4,2000",
    "M42": "M42,f|N,5:35:17.3,-5:23:28,4.0,2000",
    "M45": "M45,f|O,3:47:00,+24:07:00,1.6,2000",
    "NGC 7000": "NGC 7000,f|N,20:58:47,+44:19:48,4.0,2000",
    "NGC 2244": "NGC 2244,f|O,6:31:55,+4:56:30,4.8,2000",
}
SOLAR_SYSTEM = {
    "Jupiter": ephem.Jupiter,
    "Mars": ephem.Mars,
    "Saturn": ephem.Saturn,
    "Moon": ephem.Moon,
}

DEFAULTS = {
    "latitude": "51.5074",
    "longitude": "-0.1278",
    "target": "M31",
    "max_wind": 25.0,
    "min_altitude": 30,
    "dew_threshold": 3.0,
    "max_cloud_cover": 50.0,
    "heater_switch_id": 0,
    "ui_theme": "Deep Space Gray (Dark)",
    "use_internet_weather": True,
    "selected_mount_uri": "",
    "selected_camera_uri": "",
    "selected_heater_uri": "",
    "selected_cloud_uri": ""
}

# ------------------------------------------------------------------ DEVICE DISCOVERY
# Device URI formats stored in the config:
#   ascom://<ProgID>                         (classic Windows COM driver on this PC)
#   alpaca://<ip>:<port>/<type>/<number>     (Alpaca server on the LAN)
#   indi://<ip>:<port>/<device name>         (INDI server on the LAN)
ALPACA_DISCOVERY_PORT = 32227
INDI_PORT = 7624
NO_DEVICE = ("None / Simulation", "")

# Which dropdown each device type belongs in: mount / camera / heater / cloud
ALPACA_CATEGORY = {
    "telescope": "mount",
    "camera": "camera",
    "switch": "heater",              # dew heaters are normally exposed as switches
    "observingconditions": "cloud",
    "safetymonitor": "cloud",
}
ASCOM_TYPES = ["Telescope", "Camera", "Switch", "ObservingConditions", "SafetyMonitor"]


def local_ipv4_addresses():
    """Non-loopback IPv4 addresses of this PC (one per network adapter where possible)."""
    addrs = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                addrs.add(ip)
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))  # UDP connect sends nothing; it just picks the default adapter
        addrs.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(addrs)


def discover_ascom_local():
    """Classic ASCOM drivers installed on this Windows PC (read from the ASCOM Profile registry)."""
    if sys.platform != "win32":
        return []
    import winreg
    found = {}
    for dtype in ASCOM_TYPES:
        category = ALPACA_CATEGORY[dtype.lower()]
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                     rf"SOFTWARE\ASCOM\{dtype} Drivers", 0,
                                     winreg.KEY_READ | view)
            except OSError:
                continue
            with key:
                i = 0
                while True:
                    try:
                        progid = winreg.EnumKey(key, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        desc = winreg.QueryValue(key, progid) or progid
                    except OSError:
                        desc = progid
                    found[progid] = {
                        "label": f"[ASCOM] {desc} ({progid})",
                        "uri": f"ascom://{progid}",
                        "category": category,
                    }
    return list(found.values())


def discover_alpaca(timeout=2.0):
    """Find Alpaca servers via UDP broadcast, then list the devices each one exposes."""
    servers = {}  # (ip, port)
    local_ips = local_ipv4_addresses() or [""]
    for local_ip in local_ips:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind((local_ip, 0))  # send out of this specific adapter
            sock.settimeout(0.3)
            targets = ["255.255.255.255", "127.0.0.1"]
            if local_ip:
                targets.append(".".join(local_ip.split(".")[:3] + ["255"]))
            for t in targets:
                try:
                    sock.sendto(b"alpacadiscovery1", (t, ALPACA_DISCOVERY_PORT))
                except OSError:
                    pass
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    data, addr = sock.recvfrom(1024)
                except socket.timeout:
                    continue
                except OSError:
                    break
                try:
                    port = int(json.loads(data.decode("utf-8", errors="ignore"))["AlpacaPort"])
                except (ValueError, KeyError, TypeError):
                    continue
                servers[(addr[0], port)] = True
        except OSError:
            pass
        finally:
            if sock:
                sock.close()

    devices = []
    for (ip, port) in servers:
        try:
            r = requests.get(f"http://{ip}:{port}/management/v1/configureddevices", timeout=3)
            r.raise_for_status()
            for d in r.json().get("Value", []):
                dtype = str(d.get("DeviceType", "")).lower()
                category = ALPACA_CATEGORY.get(dtype)
                if not category:
                    continue
                num = d.get("DeviceNumber", 0)
                devices.append({
                    "label": f"[Alpaca] {d.get('DeviceName', dtype)} @ {ip}:{port} ({d.get('DeviceType')} {num})",
                    "uri": f"alpaca://{ip}:{port}/{dtype}/{num}",
                    "category": category,
                })
        except (requests.RequestException, ValueError):
            continue
    return devices


def indi_category(iface):
    """Map an INDI DRIVER_INTERFACE bitmask to one of our dropdown categories (None = not needed)."""
    if iface is None:
        return "any"          # unknown type: offer it everywhere
    if iface & 1:
        return "mount"        # TELESCOPE_INTERFACE
    if iface & 2:
        return "camera"       # CCD_INTERFACE
    if iface & 128:
        return "cloud"        # WEATHER_INTERFACE
    if iface & 32768:
        return "heater"       # AUX_INTERFACE (dew heaters, power boxes)
    return None


def query_indi_devices(host, port=INDI_PORT, listen=2.0):
    """Connect to an INDI server and list its devices."""
    buf = ""
    try:
        with socket.create_connection((host, port), timeout=3) as s:
            s.sendall(b'<getProperties version="1.7"/>')
            s.settimeout(0.5)
            end = time.time() + listen
            while time.time() < end:
                try:
                    chunk = s.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buf += chunk.decode("utf-8", errors="ignore")
    except OSError:
        return []

    names = list(dict.fromkeys(re.findall(r'device="([^"]+)"', buf)))
    devices = []
    for name in names:
        m = re.search(
            r'device="' + re.escape(name) + r'"[^>]*name="DRIVER_INFO".*?'
            r'name="DRIVER_INTERFACE"[^>]*>\s*(\d+)\s*<', buf, re.DOTALL)
        category = indi_category(int(m.group(1)) if m else None)
        if category is None:
            continue
        devices.append({
            "label": f"[INDI] {name} @ {host}:{port}",
            "uri": f"indi://{host}:{port}/{name}",
            "category": category,
        })
    return devices


def discover_indi():
    """Scan this PC and the local /24 subnet(s) for INDI servers (port 7624)."""
    hosts = {"127.0.0.1"}
    for ip in local_ipv4_addresses():
        prefix = ".".join(ip.split(".")[:3])
        hosts.update(f"{prefix}.{n}" for n in range(1, 255))

    def port_open(h):
        try:
            with socket.create_connection((h, INDI_PORT), timeout=0.4):
                return h
        except OSError:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
        open_hosts = [h for h in pool.map(port_open, sorted(hosts)) if h]

    devices = []
    for h in open_hosts:
        devices.extend(query_indi_devices(h))
    return devices


# ------------------------------------------------------------------ DEVICE CONTROL
# Everything below runs in worker threads (never on the GUI thread). Each function takes a
# `say(text)` callback that writes to the dashboard log.
HARDWARE_TIMEOUT = 180      # seconds to wait for a park to finish
POLL_SECONDS = 2.0          # how often to poll a device while waiting
ALPACA_CLIENT_ID = 4242
_alpaca_txn = itertools.count(1)


def _scheme(uri):
    if "://" not in uri:
        raise ValueError(f"Not a device URI: {uri!r}")
    return uri.split("://", 1)[0].lower()


def _wait_until(cond, what, timeout=None):
    end = time.time() + (timeout or HARDWARE_TIMEOUT)
    while time.time() < end:
        if cond():
            return
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"Timed out waiting for {what}")


# ---- Alpaca (HTTP/JSON) ----------------------------------------------------------------
def _alpaca_base(uri):
    hostport, dtype, num = uri[len("alpaca://"):].split("/")
    return f"http://{hostport}/api/v1/{dtype}/{num}"


def _alpaca_value(response):
    response.raise_for_status()
    data = response.json()
    if data.get("ErrorNumber", 0):
        raise RuntimeError(f"Alpaca error {data['ErrorNumber']}: {data.get('ErrorMessage', '')}")
    return data.get("Value")


def alpaca_get(base, method, **params):
    params.update(ClientID=ALPACA_CLIENT_ID, ClientTransactionID=next(_alpaca_txn))
    return _alpaca_value(requests.get(f"{base}/{method}", params=params, timeout=10))


def alpaca_put(base, method, **params):
    params.update(ClientID=ALPACA_CLIENT_ID, ClientTransactionID=next(_alpaca_txn))
    return _alpaca_value(requests.put(f"{base}/{method}", data=params, timeout=15))


def alpaca_park_mount(uri, say):
    base = _alpaca_base(uri)
    was_connected = bool(alpaca_get(base, "connected"))
    if not was_connected:
        alpaca_put(base, "connected", Connected="True")
    try:
        if alpaca_get(base, "atpark"):
            say("   Mount is already parked.")
            return "already parked"
        if not alpaca_get(base, "canpark"):
            raise RuntimeError("This mount does not support Park.")
        try:
            alpaca_put(base, "abortslew")
        except Exception as e:
            say(f"   (abort slew not accepted: {e})")
        alpaca_put(base, "park")
        _wait_until(lambda: alpaca_get(base, "atpark"), "the mount to finish parking")
        return "parked"
    finally:
        if not was_connected:
            try:
                alpaca_put(base, "connected", Connected="False")
            except Exception:
                pass


def alpaca_set_switch(uri, switch_id, on, say):
    base = _alpaca_base(uri)
    if not alpaca_get(base, "connected"):
        alpaca_put(base, "connected", Connected="True")   # left connected on purpose (see notes)
    count = int(alpaca_get(base, "maxswitch"))
    if not 0 <= switch_id < count:
        raise ValueError(f"Switch ID {switch_id} is out of range (device has {count} switches).")
    if not alpaca_get(base, "canwrite", Id=switch_id):
        raise RuntimeError(f"Switch {switch_id} is read-only.")
    alpaca_put(base, "setswitch", Id=switch_id, State="True" if on else "False")
    name = alpaca_get(base, "getswitchname", Id=switch_id)
    return f"'{name}' {'ON' if on else 'OFF'}"


def alpaca_read_sensor(uri, say):
    base = _alpaca_base(uri)
    dtype = uri.rsplit("/", 2)[-2]
    was_connected = bool(alpaca_get(base, "connected"))
    if not was_connected:
        alpaca_put(base, "connected", Connected="True")
    try:
        result = {"is_safe": None, "cloud_cover": None}
        if dtype == "safetymonitor":
            result["is_safe"] = bool(alpaca_get(base, "issafe"))
        elif dtype == "observingconditions":
            result["cloud_cover"] = float(alpaca_get(base, "cloudcover"))
        else:
            raise ValueError(f"'{dtype}' is not a sensor type.")
        return result
    finally:
        if not was_connected:
            try:
                alpaca_put(base, "connected", Connected="False")
            except Exception:
                pass


# ---- Classic ASCOM (Windows COM via pywin32) -------------------------------------------
def _ascom_dispatch(progid):
    try:
        import win32com.client
    except ImportError:
        raise RuntimeError("Classic ASCOM control needs Windows and pywin32 (pip install pywin32).")
    return win32com.client.Dispatch(progid)


def ascom_park_mount(uri, say):
    tel = _ascom_dispatch(uri[len("ascom://"):])
    was_connected = bool(tel.Connected)
    if not was_connected:
        tel.Connected = True
    try:
        if tel.AtPark:
            say("   Mount is already parked.")
            return "already parked"
        if not tel.CanPark:
            raise RuntimeError("This mount does not support Park.")
        try:
            tel.AbortSlew()
        except Exception as e:
            say(f"   (abort slew not accepted: {e})")
        tel.Park()
        _wait_until(lambda: bool(tel.AtPark), "the mount to finish parking")
        return "parked"
    finally:
        if not was_connected:
            try:
                tel.Connected = False
            except Exception:
                pass


def ascom_set_switch(uri, switch_id, on, say):
    sw = _ascom_dispatch(uri[len("ascom://"):])
    if not sw.Connected:
        sw.Connected = True                               # left connected on purpose (see notes)
    count = int(sw.MaxSwitch)
    if not 0 <= switch_id < count:
        raise ValueError(f"Switch ID {switch_id} is out of range (device has {count} switches).")
    if not sw.CanWrite(switch_id):
        raise RuntimeError(f"Switch {switch_id} is read-only.")
    sw.SetSwitch(switch_id, bool(on))
    return f"'{sw.GetSwitchName(switch_id)}' {'ON' if on else 'OFF'}"


def ascom_read_sensor(uri, say):
    dev = _ascom_dispatch(uri[len("ascom://"):])
    was_connected = bool(dev.Connected)
    if not was_connected:
        dev.Connected = True
    try:
        result = {"is_safe": None, "cloud_cover": None}
        try:
            result["is_safe"] = bool(dev.IsSafe)                # SafetyMonitor
        except Exception:
            pass
        try:
            result["cloud_cover"] = float(dev.CloudCover)       # ObservingConditions
        except Exception:
            pass
        if result["is_safe"] is None and result["cloud_cover"] is None:
            raise RuntimeError("Device reports neither IsSafe nor CloudCover.")
        return result
    finally:
        if not was_connected:
            try:
                dev.Connected = False
            except Exception:
                pass


# ---- INDI (XML over TCP) ---------------------------------------------------------------
_INDI_VECTOR = re.compile(
    r'<(?:def|set)(Switch|Light|Number|Text)Vector\b([^>]*)>(.*?)</(?:def|set)\1Vector>', re.DOTALL)
_INDI_ELEMENT = re.compile(
    r'<(?:def|one)(?:Switch|Light|Number|Text)\b([^>]*)>\s*(.*?)\s*</(?:def|one)(?:Switch|Light|Number|Text)>',
    re.DOTALL)


def _attrs(tag_text):
    return dict(re.findall(r'(\w+)="([^"]*)"', tag_text))


def indi_latest(buf, device, prop):
    """Latest (vector_state, {element: value}) for one INDI property in a stream of XML."""
    state, elems = None, {}
    for m in _INDI_VECTOR.finditer(buf):
        a = _attrs(m.group(2))
        if a.get("device") != device or a.get("name") != prop:
            continue
        state = a.get("state", state)
        for e in _INDI_ELEMENT.finditer(m.group(3)):
            elems[_attrs(e.group(1)).get("name")] = e.group(2)
    return state, elems


def _parse_indi(uri):
    hostport, _, device = uri[len("indi://"):].partition("/")
    host, _, port = hostport.partition(":")
    if not host or not device:
        raise ValueError(f"Bad INDI URI: {uri!r}")
    return host, int(port or INDI_PORT), device


class IndiSession:
    """Minimal INDI client for one device on one server."""

    def __init__(self, host, port, device):
        self.device = device
        self.buf = ""
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.settimeout(0.5)
        self.send(f'<getProperties version="1.7" device={quoteattr(device)}/>')

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def send(self, xml):
        self.sock.sendall(xml.encode("utf-8"))

    def pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                raise ConnectionError("INDI server closed the connection.")
            self.buf += chunk.decode("utf-8", errors="ignore")

    def wait_for(self, cond, timeout, step=0.5):
        end = time.time() + timeout
        while True:
            if cond():
                return True
            if time.time() >= end:
                return False
            self.pump(step)

    def latest(self, prop):
        return indi_latest(self.buf, self.device, prop)

    def set_switch(self, prop, element, on=True):
        self.send(f'<newSwitchVector device={quoteattr(self.device)} name={quoteattr(prop)}>'
                  f'<oneSwitch name={quoteattr(element)}>{"On" if on else "Off"}</oneSwitch>'
                  f'</newSwitchVector>')

    def ensure_connected(self, say):
        if not self.wait_for(lambda: self.latest("CONNECTION")[0] is not None, 5):
            raise RuntimeError(f"INDI device '{self.device}' was not found on that server.")
        if self.latest("CONNECTION")[1].get("CONNECT") != "On":
            say(f"   Connecting {self.device}...")
            self.set_switch("CONNECTION", "CONNECT")
            if not self.wait_for(lambda: self.latest("CONNECTION")[1].get("CONNECT") == "On", 20):
                raise TimeoutError(f"{self.device} did not connect.")
        self.pump(2)  # let the driver publish its properties


def indi_park_mount(uri, say):
    s = IndiSession(*_parse_indi(uri))
    try:
        s.ensure_connected(say)
        state, elems = s.latest("TELESCOPE_PARK")
        if state is None:
            raise RuntimeError("This INDI mount does not expose TELESCOPE_PARK (park unsupported).")
        if state == "Ok" and elems.get("PARK") == "On":
            say("   Mount is already parked.")
            return "already parked"
        if s.latest("TELESCOPE_ABORT_MOTION")[0] is not None:
            s.set_switch("TELESCOPE_ABORT_MOTION", "ABORT")
            s.pump(1)
        s.set_switch("TELESCOPE_PARK", "PARK")

        def parked():
            st, el = s.latest("TELESCOPE_PARK")
            if st == "Alert":
                raise RuntimeError("The mount reported an error while parking.")
            return st == "Ok" and el.get("PARK") == "On"

        if not s.wait_for(parked, HARDWARE_TIMEOUT, step=1.0):
            raise TimeoutError("Timed out waiting for the mount to finish parking")
        return "parked"
    finally:
        s.close()


def indi_set_switch(uri, switch_id, on, say):
    raise NotImplementedError(
        "Dew heater control over INDI is not supported (property names differ per driver). "
        "Use an ASCOM or Alpaca Switch device for the heater.")


def indi_read_sensor(uri, say):
    """Best effort: treats an overall WEATHER_STATUS state of 'Alert' as unsafe."""
    s = IndiSession(*_parse_indi(uri))
    try:
        s.ensure_connected(say)
        s.wait_for(lambda: s.latest("WEATHER_STATUS")[0] is not None, 5)
        state, _ = s.latest("WEATHER_STATUS")
        if state is None:
            raise RuntimeError("This INDI device has no WEATHER_STATUS property.")
        return {"is_safe": state != "Alert", "cloud_cover": None}
    finally:
        s.close()


# ---- Dispatch by device type -----------------------------------------------------------
def _handler(table, uri):
    scheme = _scheme(uri)
    if scheme not in table:
        raise ValueError(f"Unsupported device type '{scheme}'.")
    return table[scheme]


def park_mount_device(uri, say):
    return _handler({"ascom": ascom_park_mount, "alpaca": alpaca_park_mount,
                     "indi": indi_park_mount}, uri)(uri, say)


def set_heater_device(uri, switch_id, on, say):
    return _handler({"ascom": ascom_set_switch, "alpaca": alpaca_set_switch,
                     "indi": indi_set_switch}, uri)(uri, switch_id, on, say)


def read_sensor_device(uri, say):
    return _handler({"ascom": ascom_read_sensor, "alpaca": alpaca_read_sensor,
                     "indi": indi_read_sensor}, uri)(uri, say)


# ------------------------------------------------------------------ SKY CHECK (worker side)
def compute_altitude(lat, lon, target):
    """Current altitude of the target in degrees."""
    obs = ephem.Observer()
    obs.lat = str(lat)
    obs.lon = str(lon)
    obs.elevation = 0
    obs.date = ephem.now()
    if target in SOLAR_SYSTEM:
        body = SOLAR_SYSTEM[target]()
    elif target in DEEP_SKY:
        body = ephem.readdb(DEEP_SKY[target])
    else:
        raise ValueError(f"Unknown target '{target}'.")
    body.compute(obs)
    return math.degrees(float(body.alt))


def fetch_weather(lat, lon):
    """Current conditions from Open-Meteo. Raises on any failure."""
    r = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,dew_point_2m,wind_gusts_10m,cloud_cover,precipitation",
        "wind_speed_unit": "kmh",
    }, timeout=8)
    r.raise_for_status()
    current = r.json().get("current")
    if not current:
        raise ValueError("Weather API returned no current conditions.")
    return current


def run_sky_check(p, say):
    """Runs all safety checks. `p` is a plain dict of settings read from the UI on the GUI thread."""
    reasons = []
    dew_on = None

    alt = compute_altitude(p["lat"], p["lon"], p["target"])
    say(f"🔭 {p['target']} altitude: {alt:.1f}° (min {p['min_alt']}°)")
    if alt < p["min_alt"]:
        reasons.append(f"{p['target']} is below the {p['min_alt']}° horizon limit ({alt:.1f}°).")

    if p["use_internet"]:
        try:
            wx = fetch_weather(p["lat"], p["lon"])
        except (requests.RequestException, ValueError) as e:
            say(f"⚠️ Weather API failure: {e}")
            reasons.append("Weather data unavailable (fail-safe).")
        else:
            gust, cloud = wx.get("wind_gusts_10m"), wx.get("cloud_cover")
            temp, dew, rain = wx.get("temperature_2m"), wx.get("dew_point_2m"), wx.get("precipitation")
            say(f"🌦️ Gust {gust} km/h | Cloud {cloud}% | Temp {temp}°C | Dew pt {dew}°C | Precip {rain} mm")
            if gust is not None and gust > p["max_wind"]:
                reasons.append(f"Wind gusts {gust} km/h exceed limit {p['max_wind']} km/h.")
            if cloud is not None and cloud > p["max_cloud"]:
                reasons.append(f"Cloud cover {cloud}% exceeds limit {p['max_cloud']}%.")
            if rain is not None and rain > 0:
                reasons.append(f"Precipitation detected ({rain} mm).")
            if temp is not None and dew is not None:
                spread = temp - dew
                dew_on = spread < p["dew_delta"]
                say(f"💧 Dew spread {spread:.1f}°C (heater threshold {p['dew_delta']}°C)")

    if p["cloud_uri"]:
        try:
            sensor = read_sensor_device(p["cloud_uri"], say)
        except Exception as e:
            say(f"⚠️ Local sensor failure: {e}")
            reasons.append("Local cloud/safety sensor unreadable (fail-safe).")
        else:
            say(f"📡 Local sensor: safe={sensor['is_safe']} | cloud cover={sensor['cloud_cover']}")
            if sensor["is_safe"] is False:
                reasons.append("Local safety monitor reports UNSAFE.")
            if sensor["cloud_cover"] is not None and sensor["cloud_cover"] > p["max_cloud"]:
                reasons.append(f"Local cloud cover {sensor['cloud_cover']:.0f}% exceeds limit {p['max_cloud']}%.")

    return {"safe": not reasons, "reasons": reasons, "dew_on": dew_on}


class TaskWorker(QThread):
    """Runs fn(say) off the GUI thread. COM is initialised for this thread on Windows."""
    message = pyqtSignal(str)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        pythoncom = None
        if sys.platform == "win32":
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except Exception:
                pythoncom = None
        try:
            self.finished_ok.emit(self.fn(self.message.emit))
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}")
        finally:
            if pythoncom is not None:
                pythoncom.CoUninitialize()


class DiscoveryWorker(QThread):
    """Runs all scans off the GUI thread so the window never freezes."""
    progress = pyqtSignal(str)
    done = pyqtSignal(list)

    def run(self):
        devices = []
        for name, fn in (("ASCOM (this PC)", discover_ascom_local),
                         ("Alpaca (LAN)", discover_alpaca),
                         ("INDI (LAN)", discover_indi)):
            self.progress.emit(f"🔍 Scanning {name}...")
            try:
                found = fn()
            except Exception as e:
                self.progress.emit(f"⚠️ {name} scan failed: {e}")
                found = []
            self.progress.emit(f"   {name}: {len(found)} device(s) found")
            devices.extend(found)
        self.done.emit(devices)


MAX_PARK_RETRIES = 3
PARK_RETRY_DELAY_MS = 30 * 1000


class SkyWardenControlHub(QWidget):
    def __init__(self):
        super().__init__()
        self.automation_running = False
        self.last_check_time = None
        self.check_in_progress = False
        self.park_in_progress = False
        self.heater_busy = False
        self.heater_state = None      # None = unknown, True = on, False = off
        self.scan_worker = None
        self._tasks = set()           # keeps worker threads alive until they finish
        self.load_settings()
        self.initUI()
        self.apply_theme(self.settings.get("ui_theme", "Deep Space Gray (Dark)"))

        # --- TIMER 1: MAIN METEOROLOGICAL & NOWCAST LOOP (15 Mins) ---
        self.INTERVAL_MS = 15 * 60 * 1000
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.automated_sky_check)

        # --- TIMER 2: RAPID PHYSICAL SAFETY WATCHDOG (1 Second) ---
        self.safety_timer = QTimer(self)
        self.safety_timer.timeout.connect(self.rapid_hardware_safety_check)
        self.safety_timer.start(1000)

        self.log("🚀 [SkyWarden Initialized] 15-Minute Nowcasting & Hybrid Watchdog Active. "
                 f"Logging to '{LOG_FILE}'.")
        QTimer.singleShot(500, self.start_scan)  # auto-scan for devices shortly after startup

    # ------------------------------------------------------------------ logging
    def log(self, message, level=logging.INFO):
        logging.log(level, message)
        if hasattr(self, "log_output"):
            self.log_output.append(message)

    # ------------------------------------------------------------- worker threads
    def run_task(self, fn, on_done=None, on_fail=None):
        """Run fn(say) in a background thread; callbacks are delivered on the GUI thread."""
        worker = TaskWorker(fn)
        worker.message.connect(self.log)
        if on_done:
            worker.finished_ok.connect(on_done)
        worker.failed.connect(on_fail or (lambda msg: self.log(f"❌ {msg}", logging.ERROR)))
        self._tasks.add(worker)
        worker.finished.connect(functools.partial(self._tasks.discard, worker))
        worker.start()
        return worker

    # ----------------------------------------------------------------- settings
    def load_settings(self):
        self.settings = dict(DEFAULTS)
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    # Merge so keys missing from an older config never cause KeyError.
                    self.settings.update(loaded)
            except Exception as e:
                logging.warning(f"Could not read {CONFIG_FILE}, using defaults: {e}")

    def save_settings_from_ui(self):
        self.settings = {
            "latitude": self.lat_input.text().strip(),
            "longitude": self.lon_input.text().strip(),
            "target": self.target_combo.currentText(),
            "max_wind": self.wind_limit_box.value(),
            "min_altitude": self.alt_limit_box.value(),
            "dew_threshold": self.dew_thresh_box.value(),
            "max_cloud_cover": self.cloud_limit_box.value(),
            "heater_switch_id": self.heater_id_box.value(),
            "ui_theme": self.theme_combo.currentText(),
            "use_internet_weather": self.internet_weather_checkbox.isChecked(),
            "selected_mount_uri": self.mount_combo.currentData() or "",
            "selected_camera_uri": self.camera_combo.currentData() or "",
            "selected_heater_uri": self.heater_combo.currentData() or "",
            "selected_cloud_uri": self.cloud_combo.currentData() or ""
        }
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(self.settings, f, indent=4)
            self.log("💾 Settings saved.")
        except OSError as e:
            self.log(f"❌ Could not save settings: {e}", logging.ERROR)
        self.apply_theme(self.settings["ui_theme"])

    def apply_theme(self, theme_name):
        self.setStyleSheet(THEMES.get(theme_name, ""))
        # Clear per-widget overrides first so switching themes doesn't leave stale colours.
        for b in (self.auto_button, self.sync_button, self.abort_button):
            b.setStyleSheet("")
        if theme_name == "Standard Light Mode":
            self.auto_button.setStyleSheet("background-color: #2ec4b6; color: white; font-weight: bold; padding: 6px;")
            self.sync_button.setStyleSheet("background-color: #24a0ed; color: white; font-weight: bold; padding: 6px;")
            self.abort_button.setStyleSheet("background-color: #d90429; color: white; font-weight: bold; padding: 10px; border-radius: 6px;")
        elif theme_name == "Deep Space Gray (Dark)":
            self.abort_button.setStyleSheet("background-color: #d90429; color: white; font-weight: bold; padding: 10px;")
        # Astro Red keeps its all-red palette (no white light at night).

    # ---------------------------------------------------------------------- UI
    def initUI(self):
        self.setWindowTitle("🛰️ SkyWarden - Observatory Control Center")
        self.setFixedSize(600, 780)

        base_layout = QVBoxLayout()
        self.tabs = QTabWidget()

        self.dashboard_tab = QWidget()
        self.settings_tab = QWidget()

        self.tabs.addTab(self.dashboard_tab, "📊 Control Dashboard")
        self.tabs.addTab(self.settings_tab, "⚙️ Hardware & Configuration")

        self.create_dashboard_ui()
        self.create_settings_ui()

        base_layout.addWidget(self.tabs)
        self.setLayout(base_layout)

    def create_dashboard_ui(self):
        layout = QVBoxLayout()
        title = QLabel("SkyWarden Core Telemetry")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        coord_layout = QHBoxLayout()
        self.lat_input = QLineEdit(str(self.settings["latitude"]))
        self.lon_input = QLineEdit(str(self.settings["longitude"]))
        coord_layout.addWidget(QLabel("Lat:"))
        coord_layout.addWidget(self.lat_input)
        coord_layout.addWidget(QLabel("Lon:"))
        coord_layout.addWidget(self.lon_input)
        layout.addLayout(coord_layout)

        target_layout = QHBoxLayout()
        target_layout.addWidget(QLabel("Target Object:"))
        self.target_combo = QComboBox()
        self.target_list = ["M31", "M42", "M45", "Jupiter", "Mars", "Saturn", "Moon", "NGC 7000", "NGC 2244"]
        self.target_combo.addItems(self.target_list)
        if self.settings["target"] in self.target_list:
            self.target_combo.setCurrentText(self.settings["target"])
        target_layout.addWidget(self.target_combo)
        layout.addLayout(target_layout)

        self.sync_button = QPushButton("🔄 Force Run Verification & Sync")
        self.sync_button.clicked.connect(self.handle_manual_check)
        layout.addWidget(self.sync_button)

        self.auto_button = QPushButton("▶️ Initialize Automated Monitoring Loop")
        self.auto_button.clicked.connect(self.toggle_automation)
        layout.addWidget(self.auto_button)

        self.abort_button = QPushButton("🚨 EMERGENCY ABORT SCRIPT & PARK MOUNT")
        self.abort_button.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        self.abort_button.clicked.connect(self.trigger_emergency_abort)
        layout.addWidget(self.abort_button)

        layout.addWidget(QLabel("SkyWarden Terminal Streams:"))
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        layout.addWidget(self.log_output)

        self.dashboard_tab.setLayout(layout)

    def _make_device_combo(self, saved_uri, category):
        combo = QComboBox()
        combo.setProperty("category", category)  # mount / camera / heater / cloud
        combo.addItem(*NO_DEVICE)
        if saved_uri:
            combo.addItem(f"(saved) {saved_uri}", saved_uri)
            combo.setCurrentIndex(combo.count() - 1)
        return combo

    def create_settings_ui(self):
        layout = QFormLayout()
        layout.setVerticalSpacing(12)

        env_header = QLabel("Environmental Threshold Safeguards")
        env_header.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        layout.addRow(env_header)

        self.wind_limit_box = QDoubleSpinBox()
        self.wind_limit_box.setRange(0.0, 100.0)
        self.wind_limit_box.setValue(float(self.settings["max_wind"]))
        layout.addRow("Max Wind Gust Limit (km/h):", self.wind_limit_box)

        self.alt_limit_box = QSpinBox()
        self.alt_limit_box.setRange(0, 90)
        self.alt_limit_box.setValue(int(self.settings["min_altitude"]))
        layout.addRow("Minimum Horizon Limit (Degrees):", self.alt_limit_box)

        self.dew_thresh_box = QDoubleSpinBox()
        self.dew_thresh_box.setRange(0.0, 10.0)
        self.dew_thresh_box.setValue(float(self.settings.get("dew_threshold", 3.0)))
        layout.addRow("Dew Spread Activation Delta (°C):", self.dew_thresh_box)

        self.cloud_limit_box = QDoubleSpinBox()
        self.cloud_limit_box.setRange(0.0, 100.0)
        self.cloud_limit_box.setValue(float(self.settings.get("max_cloud_cover", 50.0)))
        layout.addRow("Max Cloud Cover (%):", self.cloud_limit_box)

        self.internet_weather_checkbox = QCheckBox("Enable Internet API Weather Validation")
        self.internet_weather_checkbox.setChecked(bool(self.settings.get("use_internet_weather", True)))
        layout.addRow("Internet Settings:", self.internet_weather_checkbox)

        theme_header = QLabel("\nUser Interface Display Customization")
        theme_header.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        layout.addRow(theme_header)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(list(THEMES.keys()))
        self.theme_combo.setCurrentText(self.settings.get("ui_theme", "Deep Space Gray (Dark)"))
        layout.addRow("Active UI View Theme Profile:", self.theme_combo)

        hw_header = QLabel("\nHardware Device Selection")
        hw_header.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        layout.addRow(hw_header)

        self.mount_combo = self._make_device_combo(self.settings.get("selected_mount_uri", ""), "mount")
        layout.addRow("Mount:", self.mount_combo)
        self.camera_combo = self._make_device_combo(self.settings.get("selected_camera_uri", ""), "camera")
        layout.addRow("Camera:", self.camera_combo)
        self.heater_combo = self._make_device_combo(self.settings.get("selected_heater_uri", ""), "heater")
        layout.addRow("Dew Heater:", self.heater_combo)

        self.heater_id_box = QSpinBox()
        self.heater_id_box.setRange(0, 63)
        self.heater_id_box.setValue(int(self.settings.get("heater_switch_id", 0)))
        layout.addRow("Dew Heater Switch ID:", self.heater_id_box)

        self.cloud_combo = self._make_device_combo(self.settings.get("selected_cloud_uri", ""), "cloud")
        layout.addRow("Cloud Sensor:", self.cloud_combo)

        self.scan_button = QPushButton("🔍 Scan for ASCOM / Alpaca / INDI Devices")
        self.scan_button.clicked.connect(self.start_scan)
        layout.addRow(self.scan_button)

        save_button = QPushButton("💾 Save Settings")
        save_button.clicked.connect(self.save_settings_from_ui)
        layout.addRow(save_button)

        self.settings_tab.setLayout(layout)

    # ---------------------------------------------------------- device discovery
    def start_scan(self):
        if self.scan_worker is not None and self.scan_worker.isRunning():
            return
        self.scan_button.setEnabled(False)
        self.scan_button.setText("⏳ Scanning...")
        self.scan_worker = DiscoveryWorker()
        self.scan_worker.progress.connect(self.log)
        self.scan_worker.done.connect(self.on_scan_done)
        self.scan_worker.start()

    def on_scan_done(self, devices):
        self.populate_device_combos(devices)
        self.scan_button.setEnabled(True)
        self.scan_button.setText("🔍 Scan for ASCOM / Alpaca / INDI Devices")
        self.log(f"✅ Device scan complete: {len(devices)} device(s) available.")

    def populate_device_combos(self, devices):
        for combo in (self.mount_combo, self.camera_combo, self.heater_combo, self.cloud_combo):
            cat = combo.property("category")
            current = combo.currentData() or self.settings.get(f"selected_{cat}_uri", "")
            combo.clear()
            combo.addItem(*NO_DEVICE)
            seen = set()
            for d in devices:
                if d["category"] in (cat, "any") and d["uri"] not in seen:
                    combo.addItem(d["label"], d["uri"])
                    seen.add(d["uri"])
            if current and current not in seen:
                combo.addItem(f"(saved, not found) {current}", current)
            combo.setCurrentIndex(max(combo.findData(current), 0))

    # ----------------------------------------------------------------- sky check
    def _read_coords(self):
        """Return (lat, lon) floats or raise ValueError with a friendly message."""
        try:
            lat = float(self.lat_input.text().strip())
            lon = float(self.lon_input.text().strip())
        except ValueError:
            raise ValueError("Latitude/Longitude must be numbers (e.g. 51.5074 / -0.1278).")
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            raise ValueError("Latitude must be -90..90 and longitude -180..180.")
        return lat, lon

    def collect_check_params(self):
        """Snapshot every setting the worker needs (read on the GUI thread)."""
        lat, lon = self._read_coords()
        return {
            "lat": lat,
            "lon": lon,
            "target": self.target_combo.currentText(),
            "min_alt": self.alt_limit_box.value(),
            "max_wind": self.wind_limit_box.value(),
            "max_cloud": self.cloud_limit_box.value(),
            "dew_delta": self.dew_thresh_box.value(),
            "use_internet": self.internet_weather_checkbox.isChecked(),
            "cloud_uri": self.cloud_combo.currentData() or "",
        }

    def handle_manual_check(self):
        self.automated_sky_check()

    def automated_sky_check(self):
        if self.check_in_progress:
            self.log("⏳ Previous sky check is still running; skipping this one.")
            return
        try:
            params = self.collect_check_params()
        except ValueError as e:
            self.log(f"❌ {e}", logging.ERROR)
            return
        self.check_in_progress = True
        self.log("🔎 Running sky verification...")
        self.run_task(lambda say: run_sky_check(params, say),
                      on_done=self._check_done, on_fail=self._check_failed)

    def _check_done(self, res):
        self.check_in_progress = False
        self.last_check_time = time.time()
        if res["safe"]:
            self.log("✅ Conditions SAFE for imaging.")
        else:
            for r in res["reasons"]:
                self.log(f"🛑 {r}", logging.WARNING)
            if self.automation_running:
                self.park_mount("Unsafe conditions")
        dew_on = res.get("dew_on")
        if dew_on is not None:
            if self.automation_running:
                self.set_heater(dew_on)
            elif dew_on:
                self.log("💧 Dew risk: heater would be switched ON (automation is not running).")

    def _check_failed(self, msg):
        self.check_in_progress = False
        self.log(f"❌ Sky check failed: {msg}", logging.ERROR)

    # ------------------------------------------------------------------ hardware
    def park_mount(self, reason, _attempt=0):
        uri = self.mount_combo.currentData() or ""
        if not uri:
            self.log(f"🅿️ Park requested ({reason}) but no mount is selected — nothing to move.", logging.WARNING)
            return
        if self.park_in_progress:
            self.log("🅿️ A park is already in progress.")
            return
        self.park_in_progress = True
        suffix = f", attempt {_attempt + 1}" if _attempt else ""
        self.log(f"🅿️ Parking mount ({reason}{suffix})...", logging.WARNING)
        self.run_task(lambda say: park_mount_device(uri, say),
                      on_done=self._park_done,
                      on_fail=functools.partial(self._park_failed, reason, _attempt))

    def _park_done(self, result):
        self.park_in_progress = False
        self.log(f"🅿️ Mount {result}.", logging.WARNING)

    def _park_failed(self, reason, attempt, msg):
        self.park_in_progress = False
        self.log(f"❌ PARK FAILED: {msg}", logging.CRITICAL)
        if attempt < MAX_PARK_RETRIES:
            self.log(f"🔁 Retrying in {PARK_RETRY_DELAY_MS // 1000} s...", logging.WARNING)
            QTimer.singleShot(PARK_RETRY_DELAY_MS, lambda: self.park_mount(reason, attempt + 1))
        else:
            self.log("🚨 Giving up on automatic parking. PARK THE MOUNT MANUALLY.", logging.CRITICAL)
            QMessageBox.critical(self, "SkyWarden", f"Could not park the mount:\n{msg}\n\nPark it manually!")

    def set_heater(self, on):
        uri = self.heater_combo.currentData() or ""
        if not uri or self.heater_busy or self.heater_state == on:
            return
        switch_id = self.heater_id_box.value()
        self.heater_busy = True
        self.log(f"💧 Switching dew heater {'ON' if on else 'OFF'}...")
        self.run_task(lambda say: set_heater_device(uri, switch_id, on, say),
                      on_done=functools.partial(self._heater_done, on),
                      on_fail=self._heater_failed)

    def _heater_done(self, on, result):
        self.heater_busy = False
        self.heater_state = on
        self.log(f"💧 Dew heater: {result}")

    def _heater_failed(self, msg):
        self.heater_busy = False
        self.log(f"❌ Dew heater control failed: {msg}", logging.ERROR)

    # ------------------------------------------------------------------- actions
    def toggle_automation(self):
        if not self.automation_running:
            self.save_settings_from_ui()
            self.automation_running = True
            self.last_check_time = time.time()   # watchdog baseline
            self.auto_button.setText("⏹️ Stop Automated Monitoring Loop")
            self.timer.start(self.INTERVAL_MS)
            self.log("▶️ Automated monitoring started (every 15 minutes).")
            self.automated_sky_check()  # run immediately
        else:
            self.automation_running = False
            self.timer.stop()
            self.auto_button.setText("▶️ Initialize Automated Monitoring Loop")
            self.log("⏹️ Automated monitoring stopped.")

    def trigger_emergency_abort(self):
        self.log("🚨 EMERGENCY ABORT triggered by user.", logging.CRITICAL)
        if self.automation_running:
            self.toggle_automation()
        self.park_mount("Emergency abort")
        QMessageBox.warning(self, "SkyWarden",
                            "Emergency abort: monitoring stopped and a park command was sent to the mount.\n"
                            "Watch the log to confirm the park finished.")

    def rapid_hardware_safety_check(self):
        """1-second watchdog: runs cheap local checks only (no network or device calls)."""
        if not self.automation_running or self.last_check_time is None:
            return
        # If the 15-minute loop has stalled (e.g. >20 min since last good check), fail safe.
        if time.time() - self.last_check_time > 20 * 60:
            self.log("⏱️ Watchdog: nowcast loop stalled >20 min.", logging.ERROR)
            self.park_mount("Watchdog timeout")
            self.last_check_time = time.time()  # avoid log spam every second

    def closeEvent(self, event):
        self.timer.stop()
        self.safety_timer.stop()
        workers = list(self._tasks)
        if self.scan_worker is not None:
            workers.append(self.scan_worker)
        for w in workers:
            if w.isRunning():
                w.wait(10000)
        event.accept()


def load_app_icon():
    """Icon from the assets folder next to this script (or next to it directly). None if missing."""
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, "assets", "skywarden.ico"), os.path.join(here, "skywarden.ico")):
        if os.path.exists(path):
            return QIcon(path)
    return None


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            # Gives SkyWarden its own taskbar identity so Windows shows our icon, not Python's.
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("skywarden.observatory.monitor")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app_icon = load_app_icon()
    if app_icon is not None:
        app.setWindowIcon(app_icon)
    window = SkyWardenControlHub()
    window.show()
    sys.exit(app.exec())
