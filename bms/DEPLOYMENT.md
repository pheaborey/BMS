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

## Vercel deployment (temporary SQLite mode)

Vercel detects the FastAPI application from `app.py` and installs the packages
listed in `requirements.txt`. Set the Vercel project's Root Directory to
`bms`; leave the build command and output directory unset.

Add these environment variables in **Project Settings > Environment Variables**
for each environment you deploy:

```text
BMS_MODE=online
BMS_DB=/tmp/boreybms.db
BMS_SESSION_SECRET=<private random value of at least 32 characters>
BMS_ADMIN_USERNAME=<initial admin username>
BMS_ADMIN_PASSWORD=<private password of at least 12 characters>
```

Optionally set `BMS_ADMIN_NAME`, `BMS_ADMIN_EMAIL`, and `BMS_COMPANY_NAME`.
The app creates the initial administrator on first startup and hides the
development demo logins in online mode. Do not put these secrets in source
control.

**This configuration is for a launch check only, not production data.**
`/tmp` is temporary and local to a Vercel function instance. Data, store
databases, and saved backups can disappear or differ between instances. Do not
enter real business data until the Neon/PostgreSQL migration is completed.
