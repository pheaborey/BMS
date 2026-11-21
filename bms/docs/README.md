# BoreyBMS project guides

The Word documents in this folder are the user-facing guides:

- `BoreyBMS User Manual.docx` — daily workflows and administration.
- `BoreyBMS Windows Installation Guide.docx` — installing the desktop app.
- `BoreyBMS Server Deployment Guide.docx` — deploying online on a Linux server.
- `BoreyBMS Source Build Guide.docx` — rebuilding the Windows installer.

The online-mode requirements are also documented in [`../DEPLOYMENT.md`](../DEPLOYMENT.md).

To regenerate the Word files after editing the guide content, install `python-docx`
separately from the application requirements and run
`py -3 docs\generate_word_guides.py` from the `bms` folder.
