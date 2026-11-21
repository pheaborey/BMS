# Offline and online modes

The BoreyBMS Windows installer runs in offline mode by default. It keeps its
SQLite database beside the installed application in `data\borey_bms.db`, and
opens the local app in the browser. Closing the browser does not stop the local
server; launching BoreyBMS again opens the running app.

Online mode runs a separate copy of the app on a server. It uses only the
absolute database path configured by `BMS_DB`, requires a private session
secret, and enables secure session cookies for HTTPS. The offline and online
databases are independent; changes do not synchronize between them.

## Linux server setup

Use a server with persistent disk storage; do not use an ephemeral/serverless
filesystem for the SQLite database. Install Python 3.10 or later, then install
the app's Python packages from `requirements.txt`. Keep the persistent data
directory writable by the account that runs the app.

Set these environment variables in the server's service manager (do not commit
the session secret into the project):

```sh
BMS_MODE=online
BMS_DB=/var/lib/boreybms/borey_bms.db
BMS_SESSION_SECRET=<a-private-random-value-at-least-32-characters>
```

Generate a secret once with:

```sh
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Start the app with `python main.py`. Online mode listens on port `8080` on all
interfaces and does not open a browser. Put it behind an HTTPS reverse proxy,
point the domain to that proxy, and restrict direct public access to port 8080.
The online store registry, branch databases, and saved backups are kept next to
the configured `BMS_DB` file.

To run the Windows installer in its normal local mode, leave `BMS_MODE`
unset (or set it to `offline`). Do not reuse the online database path or
session secret for the offline installation.
