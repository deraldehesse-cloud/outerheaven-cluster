# Paperless Agent - Outer Heaven Cluster

Autonomer Dokumenten- und Ingest-Agent für das Outer Heaven Cluster.

## Aufgaben & Schnittstellen
- **Überwachung von Eingangsordnern:** `/mnt/storage-beta/rechnungseingang/`, Scans, Inbox.
- **Extraktion & Klassifizierung:** Nutzung von lokalen Modellen (z. B. auf Node `fox` oder `outerheaven`) für OCR, Belegdatenerkennung und Kategorisierung.
- **REST-API / Ingest:** Schnittstelle zu Paperless-ngx via REST-API und Token-Authentifizierung.
- **Archivierung:** Automatischer Transfer verarbeiteter Belege nach `/mnt/storage-beta/rechnungsarchiv/`.

## Konfiguration
Siehe `config.yaml.example` für Endpunkte, Token und Pfade.
