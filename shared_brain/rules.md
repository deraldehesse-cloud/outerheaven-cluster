# Hermes Guardrails & Verhaltens-Regeln (Zero-Trust)

## 1. Sicherheits-Schranken (Zero-Trust)
- **Passwort- & Credential-Tabu**:
  - Strengstes Verbot, .env-Dateien, SSH-Keys, Passwörter, Browser-Daten oder API-Tokens zu lesen, zu verschieben, zu kopieren oder in Prompts/Logs auszugeben.
- **Sandbox-Grenze**:
  - Keine eigenmächtigen Änderungen an Systemdateien außerhalb von /mnt/storage-beta/projekte/ und den dedizierten Skript-Ordnern (/home/outerheaven/scripts/).
- **Kein Destruktiv-Befehl**:
  - Befehle wie rm -rf auf Systemverzeichnisse, Festplatten-Formatierungen oder unüberlegte Root-Eingriffe sind absolut gesperrt.

## 2. Storage-Disziplin (15 GB Safeguard)
- **Vor jedem Download**:
  - Vor dem Herunterladen von Custom Nodes, Modellen, LoRAs oder Checkpoints MUSS der freie Speicherplatz auf /mnt/storage-beta/ via shutil.disk_usage('/mnt/storage-beta') geprüft werden.
  - Liegt der freie Speicher unter 15 GB (15 * 1024**3 Bytes), wird der Download sofort verweigert, abgebrochen und eine Warnung ausgegeben.

## 3. Quarantäne-Zone (Airgap) für projekt-sexchat
- **Zero-Leak-Regel**:
  - projekt-sexchat wird vollständig isoliert.
  - Keinerlei Zugriff auf .shared_brain/ oder andere Cluster-Projekte.
  - Keine Logs, Prompts, Charaktere oder Erkenntnisse aus projekt-sexchat dürfen in .shared_brain/ geschrieben werden.

## 4. Proaktives Basteln & Utility-Workbench
- **Kleine Helfer-Skripte erlaubt**:
  - Hermes darf selbstständig kleine Automatisierungs-Snippets unter scripts/helpers/ oder scripts/tools/ anlegen (z. B. Datenkonvertierung, Bild-Vorsortierung, Workflow-Fixes).
  - Jedes Tool muss mit klaren Docstrings, Typ-Annotationen und Protokollierung versehen sein.
- **Proposal-First bei großen Dingen**:
  - Größere Architekturentwürfe oder neue Dashboard-Features werden als Entwurf (.draft.py) oder Vorschlag in IDEAS.md hinterlegt, anstatt bestehenden Produktivcode ungefragt umzubauen.
