<p align="center"><img src="assets/skywarden_icon_512.png" alt="SkyWarden icon" width="160"></p>

# SkyWarden

An observatory safety monitor for Windows. SkyWarden checks the sky and weather every 15 minutes and parks your mount when conditions turn unsafe. It can also switch your dew heater and read a local cloud or safety sensor.

It works with **ASCOM**, **Alpaca** and **INDI** equipment.

> **Status:** early version. The Alpaca and INDI code has been tested against mock servers only. The classic ASCOM code has not been tested, and nothing has been tried on real hardware. Test with simulators before trusting it with real equipment.

## What it does

- **Safety checks** every 15 minutes (and on demand):
  - Target altitude is above your minimum horizon limit.
  - Wind gusts, cloud cover and rain are within your limits (live data from Open-Meteo).
  - A local ASCOM, Alpaca or INDI safety monitor or cloud sensor reads safe.
  - If the weather lookup or sensor can't be read, SkyWarden treats conditions as unsafe.
- **Parks the mount** when conditions are unsafe, when you press the emergency button, or when the watchdog sees the check loop stall for more than 20 minutes. If a park fails, it retries 3 times and then alerts you to park by hand.
- **Controls a dew heater** (ASCOM or Alpaca Switch device): on when the temperature is within your set margin of the dew point, off otherwise.
- **Finds devices** by scanning for ASCOM drivers on the PC, Alpaca servers on the LAN and INDI servers on your subnet.
- **Three themes**, including an all-red night-vision theme.
- **Log:** everything is written to `skywarden_log.txt` and shown in the app.

## Requirements

- Windows 10 or 11
- Python 3.10 or newer
- Libraries: PyQt6, requests, ephem, pywin32 (see `requirements.txt`)

## Quick start

```
cd /d C:\skywarden
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python skywarden.py
```

See [INSTALL.md](INSTALL.md) for the full guide, hardware driver notes and troubleshooting.

## Using it

1. Open the **Hardware & Configuration** tab and click **Scan**.
2. Choose your mount, dew heater and cloud sensor, and set the **Dew Heater Switch ID**.
3. Set your safety limits and click **Save Settings**.
4. On the dashboard, check your latitude and longitude and pick a target.
5. Click **Force Run Verification** to try one check.
6. Click **Initialize Automated Monitoring Loop** to start monitoring.
7. **EMERGENCY ABORT** stops monitoring and parks the mount.

## What works with what

| Feature | ASCOM | Alpaca | INDI |
|---|---|---|---|
| Find devices | Yes (this PC only) | Yes (LAN) | Yes (your subnet) |
| Park mount | Yes | Yes | Yes |
| Dew heater | Yes | Yes | No |
| Cloud / safety sensor | Yes | Yes | Partial (weather status only) |

Classic ASCOM only finds drivers installed on the same PC. Remote ASCOM devices appear through Alpaca.

## Files

| File | Purpose |
|---|---|
| `skywarden.py` | The application |
| `requirements.txt` | Python libraries to install |
| `pyproject.toml` | Package metadata (`pip install .`) |
| `INSTALL.md` | Step-by-step install guide |
| `assets/skywarden.ico` | App icon, shown in the window and taskbar (keep the `assets` folder next to `skywarden.py`) |
| `assets/skywarden_icon_512.png` | Large icon image used in this README |
| `.github/workflows/main.yml` | Builds `SkyWarden.exe` on GitHub with PyInstaller |
| `config_skywarden.json` | Your saved settings (created on first save) |
| `skywarden_log.txt` | Activity log (created on first run) |

## Known limitations

- The INDI dew heater is not supported, because the property names differ per driver.
- The INDI weather check is a best guess: it treats a weather status of "Alert" as unsafe.
- The mount parks, but SkyWarden does not close roofs or domes.
- The device scan only covers your own subnet.
- A software safety monitor is a backup, not a replacement for hardware safeguards. Don't leave expensive equipment relying on it alone.

## Licence

No licence has been chosen yet. Add one before sharing the code.
