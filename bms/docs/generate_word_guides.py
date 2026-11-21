"""Generate the BoreyBMS Word guides using python-docx."""

from pathlib import Path

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


OUT = Path(__file__).resolve().parent
ISSUED = "17 November 2026"
GREEN = "0F6E56"
DARK = "18332A"
INK = "26312D"
MUTED = "68756F"
PALE = "EAF4F0"
GOLD = "F8F1DF"


def shade(cell, color):
    properties = cell._tc.get_or_add_tcPr()
    element = properties.find(qn("w:shd"))
    if element is None:
        element = OxmlElement("w:shd")
        properties.append(element)
    element.set(qn("w:fill"), color)


def margins(cell, top=90, start=120, bottom=90, end=120):
    properties = cell._tc.get_or_add_tcPr()
    element = properties.first_child_found_in("w:tcMar")
    if element is None:
        element = OxmlElement("w:tcMar")
        properties.append(element)
    for side, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = element.find(qn(f"w:{side}"))
        if node is None:
            node = OxmlElement(f"w:{side}")
            element.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def page_number(paragraph):
    run = paragraph.add_run()
    for kind, text in (
        ("begin", None),
        (None, " PAGE "),
        ("separate", None),
        (None, "1"),
        ("end", None),
    ):
        if text is not None:
            node = OxmlElement("w:instrText" if text == " PAGE " else "w:t")
            if text == " PAGE ":
                node.set(qn("xml:space"), "preserve")
            node.text = text
        else:
            node = OxmlElement("w:fldChar")
            node.set(qn("w:fldCharType"), kind)
        run._r.append(node)


def new_document(title, subject):
    document = Document()
    for section in document.sections:
        section.page_width = Cm(21)
        section.page_height = Cm(29.7)
        section.top_margin = Cm(1.7)
        section.bottom_margin = Cm(1.7)
        section.left_margin = Cm(1.9)
        section.right_margin = Cm(1.9)
        header = section.header.paragraphs[0]
        header.text = "BOREYBMS  |  PROJECT DOCUMENTATION"
        header.runs[0].font.size = Pt(8)
        header.runs[0].font.bold = True
        header.runs[0].font.color.rgb = RGBColor.from_string(GREEN)
        footer = section.footer.paragraphs[0]
        footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        run = footer.add_run(f"Version 1.0  |  {ISSUED}  |  Page")
        run.font.size = Pt(8)
        run.font.color.rgb = RGBColor.from_string(MUTED)
        page_number(footer)

    normal = document.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = RGBColor.from_string(INK)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.08
    for name, size, color in (
        ("Title", 29, DARK),
        ("Heading 1", 18, GREEN),
        ("Heading 2", 12.5, DARK),
    ):
        style = document.styles[name]
        style.font.name = "Calibri"
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor.from_string(color)
        style.paragraph_format.space_before = Pt(12)
        style.paragraph_format.space_after = Pt(5)
        style.paragraph_format.keep_with_next = True
    document.core_properties.title = title
    document.core_properties.subject = subject
    document.core_properties.author = "BoreyBMS Documentation"
    document.core_properties.keywords = "BoreyBMS, business management, guide, manual"
    return document


def cover(document, title, subtitle, audience):
    banner = document.add_table(rows=1, cols=1)
    banner.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = banner.cell(0, 0)
    shade(cell, GREEN)
    margins(cell, 220, 260, 220, 260)
    paragraph = cell.paragraphs[0]
    paragraph.paragraph_format.space_after = Pt(0)
    run = paragraph.add_run("BOREYBMS")
    run.font.size = Pt(11)
    run.font.bold = True
    run.font.color.rgb = RGBColor(255, 255, 255)
    run = paragraph.add_run("   BUSINESS MANAGEMENT SYSTEM")
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(220, 239, 230)

    paragraph = document.add_paragraph(style="Title")
    paragraph.paragraph_format.space_before = Pt(22)
    paragraph.add_run(title)
    paragraph = document.add_paragraph(subtitle)
    paragraph.paragraph_format.space_after = Pt(16)
    paragraph.runs[0].font.size = Pt(13)
    paragraph.runs[0].font.color.rgb = RGBColor.from_string(MUTED)

    info = document.add_table(rows=2, cols=2)
    info.alignment = WD_TABLE_ALIGNMENT.LEFT
    for row in info.rows:
        for cell in row.cells:
            shade(cell, PALE)
            margins(cell)
    info.cell(0, 0).text = "DOCUMENT VERSION"
    info.cell(0, 1).text = "ISSUED"
    info.cell(1, 0).text = "1.0"
    info.cell(1, 1).text = ISSUED
    for row_index, row in enumerate(info.rows):
        for cell in row.cells:
            for run in cell.paragraphs[0].runs:
                run.font.size = Pt(9 if row_index == 0 else 10)
                run.font.bold = row_index == 0
                run.font.color.rgb = RGBColor.from_string(GREEN if row_index == 0 else INK)

    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(14)
    run = paragraph.add_run("INTENDED AUDIENCE  ")
    run.font.bold = True
    run.font.color.rgb = RGBColor.from_string(GREEN)
    paragraph.add_run(audience)
    document.add_paragraph(
        "Developer: Mr. PHEA BOREY  |  Contact: +855 069563236  |  "
        "Telegram: t.me/boreytechnology2"
    )
    document.add_page_break()


def note(document, label, text, warning=False):
    table = document.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = table.cell(0, 0)
    shade(cell, GOLD if warning else PALE)
    margins(cell, 130, 170, 130, 170)
    paragraph = cell.paragraphs[0]
    paragraph.paragraph_format.space_after = Pt(2)
    run = paragraph.add_run(label + "  ")
    run.bold = True
    run.font.color.rgb = RGBColor.from_string("8A5A00" if warning else GREEN)
    paragraph.add_run(text)
    document.add_paragraph().paragraph_format.space_after = Pt(1)


def table(document, headers, rows):
    result = document.add_table(rows=1, cols=len(headers))
    result.alignment = WD_TABLE_ALIGNMENT.CENTER
    result.style = "Table Grid"
    header = result.rows[0]
    repeat = OxmlElement("w:tblHeader")
    repeat.set(qn("w:val"), "true")
    header._tr.get_or_add_trPr().append(repeat)
    for index, value in enumerate(headers):
        cell = header.cells[index]
        shade(cell, GREEN)
        margins(cell)
        cell.text = value
        for run in cell.paragraphs[0].runs:
            run.font.bold = True
            run.font.size = Pt(9)
            run.font.color.rgb = RGBColor(255, 255, 255)
    for row_index, values in enumerate(rows):
        cells = result.add_row().cells
        for index, value in enumerate(values):
            cell = cells[index]
            margins(cell)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            cell.text = str(value)
            if row_index % 2:
                shade(cell, "F4F7F5")
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.space_after = Pt(1)
                for run in paragraph.runs:
                    run.font.size = Pt(9)
    document.add_paragraph().paragraph_format.space_after = Pt(1)


def code(document, text):
    box = document.add_table(rows=1, cols=1)
    cell = box.cell(0, 0)
    shade(cell, "F2F5F3")
    margins(cell, 120, 150, 120, 150)
    paragraph = cell.paragraphs[0]
    paragraph.paragraph_format.space_after = Pt(0)
    paragraph.paragraph_format.line_spacing = 1.0
    run = paragraph.add_run(text.strip("\n"))
    run.font.name = "Consolas"
    run.font.size = Pt(8.5)
    document.add_paragraph().paragraph_format.space_after = Pt(1)


def content(document, sections):
    for item in sections:
        kind = item[0]
        if kind == "h1":
            document.add_heading(item[1], level=1)
        elif kind == "h2":
            document.add_heading(item[1], level=2)
        elif kind == "p":
            document.add_paragraph(item[1])
        elif kind in ("bullets", "steps"):
            style = "List Bullet" if kind == "bullets" else "List Number"
            for value in item[1]:
                document.add_paragraph(value, style=style)
        elif kind == "table":
            table(document, item[1], item[2])
        elif kind == "note":
            note(document, item[1], item[2], len(item) > 3 and item[3])
        elif kind == "code":
            code(document, item[1])


def build(filename, title, subtitle, audience, sections):
    path = OUT / filename
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing document: {path}")
    document = new_document(title, subtitle)
    cover(document, title, subtitle, audience)
    content(document, sections)
    document.save(path)
    print(f"{path} | {path.stat().st_size} bytes")


build(
    "BoreyBMS Windows Installation Guide.docx",
    "Windows Installation Guide",
    "Install and open the desktop edition",
    "Windows users installing BoreyBMS on a single computer.",
    [
        ("h1", "1. What you need"),
        ("p", "Use the BoreyBMS Windows setup file supplied with the project. The installer bundles the application and its Python runtime; Python and Inno Setup are not needed on the computer where the app is installed."),
        ("note", "DATA SAFETY", "BoreyBMS stores its SQLite data beside the installed application. Make a backup before uninstalling, moving, or replacing the installation folder.", True),
        ("h1", "2. Install BoreyBMS"),
        ("steps", [
            "Find BoreyBMS-Setup.exe in the project's bms\\dist folder, or use the copy provided by the project owner.",
            "Double-click the setup file. Continue only if you trust the copy and publisher shown by Windows.",
            "Follow the installer pages. The default destination is the current Windows user's Programs folder. Choose the optional desktop shortcut if desired.",
            "Finish setup and open BoreyBMS from the Start menu or desktop shortcut.",
            "Wait for BoreyBMS to open the sign-in page in your browser. The app runs locally on this computer.",
        ]),
        ("h1", "3. First sign-in and initial setup"),
        ("p", "A new database uses demonstration records and sample staff accounts. The sign-in page shows demo role cards for the default store."),
        ("table", ["Role", "Demo username", "Demo password"], [
            ("Administrator", "admin", "admin123"),
            ("Manager", "manager", "manager123"),
            ("Cashier", "cashier", "cashier123"),
            ("Staff", "staff", "staff123"),
        ]),
        ("note", "SECURITY", "Change or deactivate all demo passwords before entering real business information. Use a unique password for every account.", True),
        ("steps", [
            "Sign in as the administrator and open Users. Edit the demo accounts, change passwords, or deactivate accounts that are not needed.",
            "Open Settings and enter the company name, address, phone, email, tax rate, currency, language, theme, and work schedule.",
            "Create product categories, customers, suppliers, employees, and products. Add product images if useful.",
            "Create a full backup before day-to-day use.",
        ]),
        ("h1", "4. Where the app keeps data"),
        ("p", "The default install location is %LOCALAPPDATA%\\Programs\\BoreyBMS. The main database is data\\borey_bms.db. The store registry is stores.sqlite3; separate store databases are in data\\stores. Saved backups are in data\\backups."),
        ("p", "If you selected a different installation directory, look for the data folder beside the installed program. Keep the entire data folder when moving the installation or preparing a backup."),
        ("h1", "5. Daily operation"),
        ("bullets", [
            "Keep BoreyBMS running while using it. Closing the browser does not stop the local server; launching BoreyBMS again opens the running app.",
            "The offline edition is intended for use on the same computer. It does not synchronize with an online server copy.",
            "Use the Backups page regularly and keep a copy somewhere separate from the computer.",
            "Product images must be PNG, JPEG, or WebP and no larger than 5 MB.",
        ]),
        ("h1", "6. Troubleshooting"),
        ("table", ["What you see", "What to try"], [
            ("Browser did not open", "Launch BoreyBMS again. If it is already running, the app should open the existing local session."),
            ("Port 8080 is already in use", "Close the other app using that port, then launch BoreyBMS again. Do not delete the data folder to solve a port conflict."),
            ("Sign-in is rejected", "Check spelling and account status. Ask the administrator to reset your password or enable the account."),
            ("App data is missing", "Check the data folder beside the installed program and restore a verified backup. Do not overwrite the only copy."),
        ]),
        ("h1", "Developer contact"),
        ("p", "Developer: Mr. PHEA BOREY  |  Contact: +855 069563236  |  Telegram: t.me/boreytechnology2"),
    ],
)


build(
    "BoreyBMS Server Deployment Guide.docx",
    "Server Deployment Guide",
    "Deploy the online edition behind HTTPS",
    "System administrators deploying BoreyBMS on a Linux server.",
    [
        ("h1", "1. Deployment model"),
        ("p", "The online edition is separate from the Windows offline edition and uses a persistent SQLite database. Changes made in the offline installation do not synchronize to the server."),
        ("note", "IMPORTANT", "Use a Linux server with persistent local disk storage. Do not place SQLite data on an ephemeral or serverless filesystem. Keep the database, store registry, per-store databases, and backups together on persistent storage.", True),
        ("h1", "2. Prerequisites"),
        ("bullets", [
            "A Linux host with Python 3.10 or later and persistent storage.",
            "A DNS name pointed to the server and an HTTPS certificate provisioned by your organization or certificate provider.",
            "A non-root service account and a reverse proxy such as Nginx or an equivalent HTTPS gateway.",
            "A documented backup destination outside the application server.",
        ]),
        ("h1", "3. Install source and dependencies"),
        ("steps", [
            "Copy the complete bms source folder to /opt/boreybms. Keep main.py, Python modules, requirements.txt, templates, and static assets together.",
            "Create a dedicated non-login service account and a persistent data directory that it can write to.",
            "Create a virtual environment and install the application dependencies.",
        ]),
        ("code", """cd /opt/boreybms
sudo useradd --system --no-create-home --shell /usr/sbin/nologin boreybms
sudo chown -R boreybms:boreybms /opt/boreybms
sudo install -d -o boreybms -g boreybms -m 0750 /var/lib/boreybms
sudo -u boreybms python3 -m venv .venv
sudo -u boreybms .venv/bin/python -m pip install --upgrade pip
sudo -u boreybms .venv/bin/python -m pip install -r requirements.txt"""),
        ("h1", "4. Configure protected environment variables"),
        ("p", "Online mode requires BMS_MODE=online, an absolute BMS_DB path, and a private BMS_SESSION_SECRET at least 32 characters long. Set BMS_HOST to 127.0.0.1 when a local reverse proxy is used, so the app is not directly reachable from the public network."),
        ("code", """# Generate a one-time secret; keep the result private.
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# Example /etc/boreybms.env (replace the placeholder)
BMS_MODE=online
BMS_DB=/var/lib/boreybms/borey_bms.db
BMS_SESSION_SECRET=REPLACE_WITH_A_PRIVATE_RANDOM_VALUE
BMS_HOST=127.0.0.1
BMS_PORT=8080"""),
        ("p", "Keep the environment file out of source control and restrict its permissions. Make it readable only by root and the BoreyBMS service group."),
        ("h1", "5. Run with systemd"),
        ("p", "Create /etc/systemd/system/boreybms.service. Adjust the user, group, and paths to match the server."),
        ("code", """[Unit]
Description=BoreyBMS online service
After=network.target

[Service]
Type=simple
User=boreybms
Group=boreybms
WorkingDirectory=/opt/boreybms
EnvironmentFile=/etc/boreybms.env
ExecStart=/opt/boreybms/.venv/bin/python /opt/boreybms/main.py
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target"""),
        ("code", """sudo systemctl daemon-reload
sudo systemctl enable --now boreybms
sudo systemctl status boreybms
sudo journalctl -u boreybms -n 100 --no-pager"""),
        ("h1", "6. Put HTTPS in front of the app"),
        ("p", "Configure the reverse proxy to terminate TLS and forward requests to http://127.0.0.1:8080. Preserve the host and client IP headers and set X-Forwarded-Proto to https. Obtain and renew the TLS certificate using your normal server procedure."),
        ("code", """# Nginx location block inside the HTTPS virtual host
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
}"""),
        ("note", "NETWORK", "Expose HTTPS through the reverse proxy. Do not expose port 8080 directly to the public internet. If the proxy is on another host, bind and firewall the app so only that trusted proxy can reach it.", True),
        ("h1", "7. Verify and secure the deployment"),
        ("steps", [
            "Confirm the local health endpoint returns HTTP 200: http://127.0.0.1:8080/health.",
            "Open the public HTTPS address and confirm there are no browser certificate warnings.",
            "Sign in with the administrator account and immediately change or deactivate demonstration credentials before exposing the service.",
            "Create a full backup and verify it can be downloaded and stored off the server.",
            "Document who can access the host, database directory, environment file, and backup copies.",
        ]),
        ("h1", "8. Backups, upgrades, and recovery"),
        ("bullets", [
            "Use the administrator Backups page to create a full backup for each store. Copy backups to protected storage outside the server.",
            "The SQLite database path is BMS_DB. The store registry, branch databases, and saved backups are maintained beside it.",
            "Before an upgrade, create and verify a backup, stop the service, update the source, install changed requirements, and restart the service.",
            "Do not delete or replace the data directory during an application update. Test restores on a separate copy before relying on them.",
        ]),
        ("h1", "9. Operations and limits"),
        ("bullets", [
            "Use a persistent disk and a single controlled service instance for each SQLite database.",
            "Monitor free disk space, service logs, backup completion, and HTTPS certificate renewal.",
            "The online database is independent from every offline desktop database; plan any migration explicitly.",
            "Never commit BMS_SESSION_SECRET or other private configuration to source control.",
        ]),
        ("h1", "Developer contact"),
        ("p", "Developer: Mr. PHEA BOREY  |  Contact: +855 069563236  |  Telegram: t.me/boreytechnology2"),
    ],
)


build(
    "BoreyBMS Source Build Guide.docx",
    "Source Build Guide",
    "Rebuild the Windows executable and installer",
    "Developers maintaining or packaging the BoreyBMS source project.",
    [
        ("h1", "1. Build outputs and requirements"),
        ("p", "Build from the bms source directory on Windows. The build script creates a standalone executable and an Inno Setup installer."),
        ("table", ["Requirement", "Purpose"], [
            ("Windows computer", "Runs the Windows packaging process."),
            ("Python 3.10 or later and the py launcher", "Installs dependencies and runs PyInstaller."),
            ("Inno Setup 6", "Compiles the final setup executable."),
            ("Complete source folder", "Includes the app, templates, static assets, and packaging files."),
        ]),
        ("h1", "2. Prepare the build computer"),
        ("steps", [
            "Install Python 3.10 or later and confirm py -3 --version works.",
            "Install Inno Setup 6. The build script searches PATH and standard installation locations for ISCC.exe.",
            "Open PowerShell in the project's bms folder.",
            "Close any running MeridianBMS.exe before building. PyInstaller must replace that output executable; a running process can cause an access-denied build failure.",
        ]),
        ("code", """cd "C:\\path\\to\\BMS\\bms"
py -3 --version
py -3 -m pip install -r requirements.txt"""),
        ("h1", "3. Rebuild the app and installer"),
        ("steps", [
            "Run the project build script. It installs requirements and PyInstaller, bundles the app using BoreyBMS.spec, and compiles the setup program using BoreyBMS.iss.",
            "Wait for the script to report success. Do not distribute outputs from a failed build.",
            "Review output timestamps and file sizes in the dist folder.",
        ]),
        ("code", ".\\build_windows_exe.bat"),
        ("table", ["Output", "Description"], [
            ("dist\\MeridianBMS.exe", "Standalone Windows application executable."),
            ("dist\\BoreyBMS-Setup.exe", "Installer for distribution to Windows computers."),
        ]),
        ("h1", "4. Source files that affect packaging"),
        ("table", ["File or folder", "Role"], [
            ("main.py and app.py", "Application entry point and web behavior."),
            ("templates\\", "Server-rendered HTML screens."),
            ("static\\", "CSS, JavaScript, and visual assets."),
            ("db.py and stores.py", "SQLite schema, migrations, database path, and store registry."),
            ("i18n.py", "English, Vietnamese, French, and Khmer strings."),
            ("BoreyBMS.spec", "PyInstaller inputs and bundled assets."),
            ("BoreyBMS.iss", "Installer destination, shortcuts, and migration behavior."),
        ]),
        ("note", "DATA SAFETY", "The build script is not a database reset tool. Keep data and backup folders separate from source/output cleanup. Never delete a data folder as a workaround for a packaging error.", True),
        ("h1", "5. Verify the release"),
        ("steps", [
            "Confirm both output files exist and have current timestamps.",
            "Install the setup file on a test Windows account or test computer.",
            "Launch the app, verify the sign-in page and developer contact block, sign in, and test a safe workflow such as opening Products.",
            "Verify the product image uploader and catalogue if product-image changes were included.",
            "Create a backup before replacing an installation containing real business data.",
        ]),
        ("h1", "6. Troubleshooting"),
        ("table", ["Symptom", "Recommended action"], [
            ("Access denied while replacing MeridianBMS.exe", "Close the running executable, confirm it has stopped, then retry the build."),
            ("ISCC.exe not found", "Install Inno Setup 6 and rerun the build script."),
            ("Python package install fails", "Confirm package-index access and that py -3 selects the intended Python."),
            ("Installer builds but app fails to start", "Test the standalone executable and inspect the files included in BoreyBMS.spec. Keep the previous release and a database backup."),
        ]),
        ("h1", "Developer contact"),
        ("p", "Developer: Mr. PHEA BOREY  |  Contact: +855 069563236  |  Telegram: t.me/boreytechnology2"),
    ],
)


build(
    "BoreyBMS User Manual.docx",
    "User Manual",
    "A practical guide to daily work and administration",
    "Administrators, managers, cashiers, and staff using BoreyBMS.",
    [
        ("h1", "1. Welcome to BoreyBMS"),
        ("p", "BoreyBMS brings product records, stock, purchases, point-of-sale sales, customers, suppliers, expenses, staff attendance, invoices, reports, and backups into one browser-based business desk."),
        ("table", ["Area", "Typical use"], [
            ("Dashboard", "Review sales, low-stock alerts, open purchases, recent tickets, and attendance."),
            ("Catalog and stock", "Maintain categories and products, track stock, attach product images, and print a catalogue."),
            ("Trade", "Create purchase orders, receive goods, record sales, and view invoices."),
            ("People and operations", "Maintain users, employees, customers, suppliers, expenses, and attendance."),
            ("System", "Configure company settings, role permissions, stores, and database backups."),
        ]),
        ("h1", "2. Sign in and understand access"),
        ("p", "A new default database uses demonstration accounts. These credentials are for sample/demo use only."),
        ("table", ["Role", "Username", "Password"], [
            ("Administrator", "admin", "admin123"),
            ("Manager", "manager", "manager123"),
            ("Cashier", "cashier", "cashier123"),
            ("Staff", "staff", "staff123"),
        ]),
        ("note", "SECURITY FIRST", "Before entering real records, change the demonstration passwords or deactivate unused accounts. Use a strong, unique password and give each person an individual account.", True),
        ("p", "Menus and write actions depend on role permissions. The administrator can manage users, configure menu access, create stores, and manage backups. Ask an administrator if a menu item is hidden or an action is denied."),
        ("h1", "3. Recommended first-time setup"),
        ("steps", [
            "Open Settings and enter your company profile, tax rate, display currency, language, theme, and attendance schedule.",
            "Review Users. Replace demo passwords and create accounts for actual staff.",
            "Create product Categories before adding products.",
            "Add Suppliers and Customers as needed.",
            "Add Employees and confirm their department, position, and active status.",
            "Create Products with a name, category, cost, selling price, unit, reorder level, and opening stock.",
            "Create a full backup before recording live sales or purchases.",
        ]),
        ("h1", "4. Products, images, and the catalogue"),
        ("h2", "Add or edit a product"),
        ("steps", [
            "Open Products and choose the action to create a product.",
            "Enter the product name and select an existing category. Leave SKU blank to let the app generate a code.",
            "Enter cost, selling price, unit, reorder level, and opening stock. Opening stock is entered only when creating a product.",
            "Choose Browse files or drag an image into the image area. PNG, JPEG, and WebP files up to 5 MB are supported.",
            "Check the preview and filename, then save. When editing a product, choose a replacement image or use Remove current image.",
        ]),
        ("p", "The product list shows an image thumbnail or fallback initials beside the product name and SKU. Image bytes are stored in the product database and included in a full database backup."),
        ("h2", "Print the product catalogue"),
        ("steps", [
            "Open Product catalogue from the product area.",
            "Review the active products, images, and prices.",
            "Choose Print / Save PDF in the browser print dialog to print or save a PDF.",
        ]),
        ("h1", "5. Point of sale and sales records"),
        ("steps", [
            "Open Point of sale. Search for a product or filter by category, then select products to add them to the ticket.",
            "Adjust quantities and choose a customer if applicable; customer selection is optional for walk-in sales.",
            "Enter any discount, select Cash, Card, or Transfer, and enter the amount tendered.",
            "Review subtotal, configured tax, total, and change. Select Take payment to record the sale.",
            "Open Sales or Invoices to find the transaction and view or print its invoice.",
        ]),
        ("note", "STOCK", "A completed sale reduces product stock. A paid sale can be voided only by a user with write permission; voiding returns its items to stock.", True),
        ("h1", "6. Purchases and stock control"),
        ("h2", "Create and receive a purchase"),
        ("steps", [
            "Open Purchases and choose New purchase.",
            "Select a supplier, add active products, and enter quantities and unit costs.",
            "Save the order. It starts as Ordered and does not increase stock yet.",
            "When goods arrive, open the order and choose Receive goods. The app records the receipt and updates stock.",
        ]),
        ("p", "An open order may be cancelled before it is received. Received orders cannot be received again."),
        ("h2", "Make a manual stock adjustment"),
        ("steps", [
            "Open Stock and select a product and the direction (in or out).",
            "Enter a positive quantity and a clear reason. A note is required for every adjustment.",
            "Save and review the resulting stock movement.",
        ]),
        ("h1", "7. Customers, suppliers, employees, and expenses"),
        ("bullets", [
            "Customers: record names and optional phone, email, company, address, and notes. Link a customer to a POS sale when appropriate.",
            "Suppliers: record contact details and use them when creating purchase orders.",
            "Employees: maintain staff profiles, department, position, salary, hire date, and active status.",
            "Expenses: record a category, amount, date, and note. Check entries before saving because expenses affect profit reporting.",
        ]),
        ("h1", "8. Attendance"),
        ("p", "Open Attendance and choose the work date. Use the available action for each employee: check in, check out, absent, or leave. Check-out requires a prior check-in. Administrators can adjust recorded times. Work hours and break times are configured in Settings."),
        ("h1", "9. Dashboard, reports, and exports"),
        ("bullets", [
            "The Dashboard summarizes the current day and month, recent sales, low stock, inventory value at cost, open purchases, and attendance.",
            "Reports supports a date range and summarizes paid revenue, cost of goods sold, gross profit, expenses, net result, purchases, payment methods, top products, and stock.",
            "Use Export CSV on the applicable report to open data in a spreadsheet. Keep exports protected because they may contain business or customer details.",
            "Changing the displayed currency changes formatting; it does not convert historical amounts.",
        ]),
        ("h1", "10. Settings, users, and stores"),
        ("h2", "Company settings"),
        ("p", "Settings contains the company profile, tax rate, language, theme, currency, and work schedule. The POS applies the configured tax rate to the discounted subtotal. Choose the currency before routine use; changing the display is not a conversion."),
        ("h2", "User and role administration"),
        ("p", "Create an individual account for each team member. Set a role, password, and active status. When editing a user, leave the password field blank to keep it. Administrators can configure view and write access by role; write access is available only when view access is enabled."),
        ("h2", "Multiple stores"),
        ("p", "Administrators can create a store from the Stores page. Each store has a separate database and independent records. Opening another store requires signing in again. Backups and saved records remain scoped to the selected store."),
        ("h1", "11. Back up and restore data"),
        ("steps", [
            "Open Backups as an administrator.",
            "Choose a full database backup, or select specific tables when a partial backup is appropriate.",
            "Choose Download backup to save a copy locally, or Save backup to store it on the server for the selected store.",
            "Keep downloaded copies in a secure location separate from the computer or server running BoreyBMS.",
            "To restore, select a saved backup or upload a ZIP backup. Confirm the warning and wait for restore to finish.",
        ]),
        ("note", "RESTORE WARNING", "Restoring replaces data in the selected database scope. Create a fresh backup first, verify the store selection, and use only a backup from a trusted source.", True),
        ("h1", "12. Common questions"),
        ("table", ["Question", "Answer"], [
            ("Why can I not see a menu?", "The signed-in role may not have view permission. Ask the administrator to review Permissions."),
            ("Why did stock not increase after creating a purchase?", "Creating an order does not change stock. Open the order and receive the goods."),
            ("Why can I not delete a product?", "A product referenced by a sale or purchase is deactivated rather than deleted so history remains intact."),
            ("Why does the offline copy not show server changes?", "Offline and online databases are separate and do not synchronize automatically."),
            ("Where are images stored?", "Product images are kept in the product database and included in full backups."),
        ]),
        ("h1", "Developer contact"),
        ("p", "Developer: Mr. PHEA BOREY  |  Contact: +855 069563236  |  Telegram: t.me/boreytechnology2"),
    ],
)
