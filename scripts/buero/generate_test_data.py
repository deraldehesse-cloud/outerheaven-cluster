#!/usr/bin/env python3
"""Generates test invoice and test job posting for end-to-end verification."""

from PIL import Image, ImageDraw
from pathlib import Path

# 1. Test Invoice PNG
img = Image.new("RGB", (900, 1100), color=(255, 255, 255))
d = ImageDraw.Draw(img)

invoice_lines = [
    "Hetzner Online GmbH",
    "Industriestr. 25, 91710 Gunzenhausen",
    "",
    "RECHNUNG",
    "Rechnungsnummer: RE-2026-98765",
    "Rechnungsdatum: 2026-09-08",
    "Faelligkeitsdatum: 2026-09-22",
    "",
    "Positionen:",
    "1. Dedicated Server EX44 - 49.00 EUR",
    "2. IPv4 Zusatzadresse - 3.50 EUR",
    "3. Backup Space 1TB - 5.40 EUR",
    "",
    "Gesamtbetrag: 57.90 EUR",
    "",
    "Zahlungsinformationen:",
    "IBAN: DE89760700240123456789",
    "BIC: BYLADEM1GUN",
    "Verwendungszweck: RE-2026-98765 Kd-Nr 4321"
]

y = 60
for line in invoice_lines:
    d.text((60, y), line, fill=(0, 0, 0))
    y += 40

out_inv = Path("/mnt/storage-beta/rechnungseingang/rechnung_hetzner_test.png")
img.save(out_inv)
print(f"✔ Test invoice created at {out_inv}")

# 2. Test Job Posting TXT
job_text = """
Stellenanzeige: Senior DevOps & Linux Systems Architect (m/w/d)

Unternehmen: CyberDynamics Solutions GmbH
Standort: Remote / Frankfurt am Main
Anstellungsart: Vollzeit

Über uns:
CyberDynamics Solutions betreibt hochverfügbare IT-Infrastrukturen und autonome Datenpipelines für moderne Enterprise-Kunden.

Deine Aufgaben:
- Administration und kontinuierliche Optimierung heterogener Linux-Servercluster und GPU-Rechenknoten.
- Entwurf und Implementierung autonomer Daten- und KI-Pipelines mit lokalen LLMs und Python.
- Konzeption von Hochverfügbarkeits- und Backup-Strategien mit Systemd-Automatisierung und Tailscale-Mesh-Netzwerken.
- Begleitung von CI/CD-Prozessen und API-Entwicklung.

Dein Profil:
- Fundierte mehrjährige Erfahrung mit Linux-Infrastrukturen und Systemautomatisierung.
- Sehr gute Programmierkenntnisse in Python, Bash und REST-APIs.
- Erfahrung mit lokalen KI-Modellen (Ollama, HuggingFace) und Container-Technologien.
- Pragmatische Macher-Mentalität und ausgeprägte Problemlösungskompetenz.

Wir bieten:
- 100% Remote-Work und flexible Arbeitszeiten.
- Neueste Hardware und Zugriff auf leistungsstarke GPU-Cluster.
- Flache Hierarchien und eigenverantwortliche Projekte.

Bewerbung bitte per E-Mail an: jobs@cyberdynamics-solutions.de
"""

out_job = Path("/mnt/storage-beta/jobangebote/cyberdynamics_devops_job.txt")
out_job.write_text(job_text.strip(), encoding="utf-8")
print(f"✔ Test job posting created at {out_job}")
