---
name: buero_hermes
description: "Büro-Hermes: Revisionssichere Archivierung (10 Jahre), Mail-Filter (Spam/Werbung), Phishing-Schutz, Mahnungs- & Inkasso-Guard mit rechtssicheren Musterschreiben, OCR-Scan-Indexierung mit qwen:vram, Textentwürfe mit darkidol:ram und systemd Autopilot Timer."
---

# 📁 Büro-Hermes: Büro-Organisation, Archivierung & Inkasso-Guard

Das Modul `buero_hermes.py` automatisiert den Büro- und Dokumenten-Workflow für den Hermes-Agenten auf Node **Fox**.
Es arbeitet primär mit lokalen Dateisystemoperationen (`os`, `pathlib`, `shutil`) im über rclone gemounteten Google Drive (`/home/fox/gdrive/Agenten-Drive/`).

---

## 📂 Verzeichnisstruktur

- Zielverzeichnis: `/home/fox/gdrive/Agenten-Drive/Büro/Büro Hermes/` (bzw. `Büro／Hermes`)
- Lokale Unterordner:
  * `schriftlich/`: Eingehende gescannte Dokumente, Rechnungen & Mahnungen.
  * `E-Mail/`: Automatisch aus Gmail extrahierte Rechnungs-PDFs.
  * `Schriftsätze/`: Automatisch generierte rechtssichere Widersprüche & Ratenzahlungsangebote.
  * `Fixkosten/`: Laufende Verträge, Abonnements, Daueraufträge.
  * `Bewerbungsunterlagen/`: Lebensläufe, Zeugnisse, Anschreiben.
  * `Archiv/YYYY/`: Revisionssichere 10-Jahre-Langzeitablage nach GoBD (`Archiv/YYYY/E-Mail/` und `Archiv/YYYY/schriftlich/`).
  * `Werbung/`: Strukturierte Ablage für Newsletter und Werbebenachrichtigungen.
  * `Isoliert/`: Sicherheitsquarantäne für Phishing-Verdachtsfälle und abgewehrte unzulässige Anhänge.

---

## 🛡️ IT-Sicherheit & Phishing-Guard

- **Null-Toleranz bei Links**: Niemals Links in E-Mails anklicken, folgen oder über Scraper/Browser aufrufen.
- **Strikte Dateiformat-Whitelist**: Es werden ausschließlich `.pdf`, `.png`, `.jpg`, `.jpeg` akzeptiert.
- **Magic-Byte-Prüfung**: Dateiinhalte werden auf echte Signaturen (`%PDF-`, `PNG`, `JPEG`) geprüft; manipulierte oder getarnte Binärdateien werden sofort abgewiesen.
- **Gefahrenabwehr**: `.exe`, `.scr`, `.bat`, `.vbs`, `.js`, `.zip`, etc. werden ausnahmslos blockiert und in `Isoliert/` isoliert.
- **Phishing-Erkennung**: Domain-Spoofing, Drängungs-Schlagwörter und Link-Spam führen zur sofortigen Quarantäne.

---

## ⚖️ Mahnungs- & Inkasso-Guard

- **Früherkennung**: Automatische Identifikation von Mahnstufen und Inkassofällen anhand der Kernschlagwörter (*Mahnung*, *Inkasso*, *Aktenzeichen*, *Hauptforderung*, *Verzugszinsen*, *Frist*, *PAIR Finance*, *Creditreform*, *EOS*, etc.).
- **Dringlichkeits-Einstufung**: Berechnung verbleibender Tage bis zum Fristablauf (`🚨 ÜBERFÄLLIG`, `⚠️ DRINGEND`, `ℹ️ OFFEN`).
- **Standardisierte Musterschreiben**:
  * **Widerspruch**: Bestreiten unberechtigter Inkassogebühren nach § 4 Abs. 5 RDGEG / § 254 BGB, Rüge der Bevollmächtigung (§ 174 BGB), Anforderung detaillierter Forderungsaufstellung (§ 11a / § 13a RDG) sowie Untersagung der Datenübermittlung an Auskunfteien (SCHUFA etc.) gem. Art. 21 DSGVO / § 31 BDSG.
  * **Ratenzahlung**: Verbindliches Angebot einer monatlichen Rate **ausdrücklich OHNE Anerkenntnis einer Rechtspflicht** und ohne Schuldanerkenntnis (§§ 780, 781 BGB), Zinsstopp, Ruhen von Vollstreckungsmaßnahmen und SCHUFA-Verzicht.
- **Speicherort**: Fertige Schriftsätze werden als formatierte Markdown-Dateien automatisch in `Schriftsätze/` abgelegt.

---

## ⚖️ Revisionssichere 10-Jahre-Archivierung

- **Kein Belegverlust**: Keine Rechnungen oder offiziellen Belege werden gelöscht.
- **90-Tage-Regel**: Vorgänge, die älter als 90 Tage sind, werden automatisch nach Kalenderjahr in `Archiv/YYYY/E-Mail/` bzw. `Archiv/YYYY/schriftlich/` verschoben.
- **Kontext-Schutz**: Für `qwen:vram` (8192 Token Kontext) werden standardmäßig nur die letzten 90 Tage als aktiver Arbeitsbestand geladen (`get_active_workspace`).

---

## 🧠 Modell-Routing

| Aufgabe | Modell | Kontext | Zweck |
| :--- | :--- | :--- | :--- |
| **Extraktion & Indexierung** | `qwen:vram` | 8192 | Schnelle Erkennung von Datum, Gläubiger, Betrag und Aktenzeichen aus PDF/OCR-Text |
| **Deep-Textentwürfe** | `darkidol:ram` | 8192 | Juristisch fundierte Schriftsätze (Widersprüche, Ratenzahlungen, Bewerbungen) mit automatischem Musterschreiben-Fallback |

---

## ⏱️ Systemd User Timer (Autopilot auf fox)

Konfiguration unter `~/.config/systemd/user/`:
- `buero-hermes.service`: Führt `/usr/bin/python3 /home/fox/scripts/buero_hermes.py run-all` aus und loggt nach `~/.hermes/logs/buero_hermes.log`.
- `buero-hermes.timer`: Startet 5 Minuten nach Boot und läuft wiederkehrend alle 30 Minuten (`Persistent=true`).

Aktivierung:
```bash
systemctl --user daemon-reload
systemctl --user enable --now buero-hermes.timer
loginctl enable-linger fox
```

---

## 🚀 CLI-Nutzung

```bash
# Status der Verzeichnisse (inkl. Schriftsätze) anzeigen
python3 scripts/buero_hermes.py status

# Status-Report über offene Mahnungen, Inkassofälle und Fristen
python3 scripts/buero_hermes.py summary [--json]

# Routine 1: Gmail Ingest (mit Spam-, Werbe- und Phishing-Filter)
python3 scripts/buero_hermes.py sync-mail [--max 25] [--no-mark-read]

# Routine 2: Gescannte PDFs in 'schriftlich/' analysieren und umbenennen
python3 scripts/buero_hermes.py index-scans [--dry-run]

# Revisionssichere Archivierung von Dokumenten älter als 90 Tage nach Archiv/YYYY/
python3 scripts/buero_hermes.py archive [--days 90] [--dry-run]

# Gesamtlauf (Mail-Sync + Scan-Indexierung + Archivierung + Mahnungs-Summary)
python3 scripts/buero_hermes.py run-all

# Schriftsatz via Inkasso-Guard & darkidol:ram erstellen:
# 1. Widerspruch gegen Inkassoforderung (z.B. PAIR Finance)
python3 scripts/buero_hermes.py draft --type widerspruch --case "PAIR Finance"

# 2. Ratenzahlungsangebot (ohne Schuldanerkenntnis) mit 35,00 € Rate
python3 scripts/buero_hermes.py draft --type ratenzahlung --case "Telekom" --rate 35,00

# 3. Manuelle Datei oder Kontext
python3 scripts/buero_hermes.py draft --type widerspruch --file /pfad/zum/scan.pdf [--output antwort.md]

# Hintergrundüberwachung (Manueller Watcher Daemon)
python3 scripts/buero_hermes.py watch --interval 120
```

---

## 🤖 AutoGen Integration

```python
import buero_hermes

# Tools für AutoGen registrieren
tools = buero_hermes.get_autogen_tools()

# Verfügbare Funktionen:
# tools['sync_gmail_invoices']['function']()
# tools['index_scanned_documents']['function']()
# tools['archive_aged_documents']['function'](days=90)
# tools['get_active_workspace']['function']()
# tools['get_mahnungen_summary']['function']()
# tools['draft_inkasso_response']['function'](case_name, draft_type="widerspruch")
# tools['draft_legal_response']['function'](context_text, category="widerspruch")
# tools['get_office_status']['function']()
```
