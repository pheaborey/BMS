# BoreyBMS

BoreyBMS is a browser-based business management app backed by SQLite. It
supports inventory, sales, purchases, customers, suppliers, expenses,
attendance, reports, per-store databases, and administrator backup/restore.
Products can include an image and be laid out in a printable catalogue with
their names and prices.

## Windows installation

Run `dist\BoreyBMS-Setup.exe`. The installer includes the Python runtime and
application dependencies. No separate Python installation is needed.

## Run from source

Install Python 3.10 or later, then from this directory run:

```powershell
py -3 -m pip install -r requirements.txt
py -3 main.py
```

The local app opens in the default browser. Its main database is
`data\borey_bms.db`; the store registry and per-store databases are also kept
in `data\`. Existing `meridian.db` data is migrated automatically when the app
starts.

## Build the Windows installer

Install Python 3.10 or later and Inno Setup 6 on the build computer, then run
`build_windows_exe.bat`. The installer is written to
`dist\BoreyBMS-Setup.exe`.

## Online deployment

The server mode uses an independent, persistent SQLite database and requires
HTTPS. Configure `BMS_MODE`, `BMS_DB`, and `BMS_SESSION_SECRET` as described in
[`DEPLOYMENT.md`](DEPLOYMENT.md). Online and offline data are separate and do
not synchronize automatically.

## Project guides

The Word guides in [`docs`](docs) cover everyday use, Windows installation,
Linux server deployment, and rebuilding the Windows installer from source.
The login page also displays developer contact information.
