# Installing SkyWarden on Windows

## 1. Install Python
1. Download Python 3.10 or newer from https://www.python.org/downloads/windows/
2. In the installer, tick **"Add python.exe to PATH"**, then click Install.
3. Check it worked. Open **Command Prompt** and run:
   ```
   python --version
   ```
   (If `python` isn't found, try `py --version` and use `py` instead of `python` below.)

## 2. Put the files in a folder
Create the folder `C:\skywarden` and copy these two files into it:
- `skywarden.py`
- `requirements.txt`

## 3. Install the libraries
In Command Prompt:
```
cd /d C:\skywarden
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```
This installs PyQt6 (the window), requests, ephem (target altitude) and pywin32 (classic ASCOM).

## 4. Run it
```
cd /d C:\skywarden
venv\Scripts\activate
python skywarden.py
```
Always start it from this folder. It saves `config_skywarden.json` and `skywarden_log.txt` in the folder you run it from.

On launch it scans for devices automatically. You can rescan any time from the **Hardware & Configuration** tab.

## 5. Install your hardware drivers (as needed)
- **ASCOM:** install the ASCOM Platform from https://ascom-standards.org, then your equipment's ASCOM driver. Installed drivers show up in the dropdowns after a scan.
- **Alpaca:** the Alpaca server (on this PC or another machine on your network) must be running.
- **INDI:** an INDI server must be running on port 7624.

## 6. First-time setup
1. Open **Hardware & Configuration**.
2. Click **Scan**, then choose your mount, dew heater and cloud sensor.
3. Set the **Dew Heater Switch ID** to match your heater's switch number.
4. Check your latitude/longitude on the dashboard.
5. Click **Save Settings**.

**Test with simulators before real use.** Install the ASCOM Telescope and Switch simulators, press **Force Run Verification**, then try the emergency button while watching the log. Do this before you trust it with real equipment.

## Troubleshooting
| Problem | Fix |
|---|---|
| `python` is not recognised | Reinstall Python and tick "Add to PATH", or use `py` instead of `python`. |
| `No module named PyQt6` (or similar) | Activate the venv (`venv\Scripts\activate`), then run `pip install -r requirements.txt` again. |
| No devices found | Allow Python through Windows Firewall on your **private** network (needed for Alpaca discovery). INDI is only found on your own subnet. |
| "needs Windows and pywin32" in the log | Run `pip install pywin32` inside the venv. |
| ASCOM "Class not registered" error | The driver may be 32-bit only. Install 32-bit Python and repeat steps 3 and 4. |
| Safety checks fail with "weather unavailable" | The PC needs internet access, or untick "Enable Internet API Weather Validation". |
