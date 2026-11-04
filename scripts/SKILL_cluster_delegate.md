---
name: cluster-delegate
description: "Cluster workload delegation and handover queue between Fox and Outer Heaven."
version: 1.1.0
author: Outer Heaven Cluster
license: MIT
metadata:
  hermes:
    tags: [Cluster, Delegation, Offload, Compute, Queue, OuterHeaven, Fox]
---

# Cluster Delegate & Handover Queue (Outer Heaven ↔ Fox)

Dieses Werkzeug steuert die bidirektionale Lastverteilung und die asynchrone Task-Handover-Warteschlange zwischen Node **Fox** (Standby-Worker & Gateway) und Node **Outer Heaven** (Heavy Compute).

## 🛡️ Anti-Halluzination & Ehrliches Guardrail
* **Keine Scheinerledigung:** Wenn eine Aufgabe Bild-Downloads, Binärdateien-Handling oder komplexes mehrstufiges Reasoning erfordert, für das dir auf der Worker Node Fox lokale Werkzeuge fehlen:
  * Gib **NIEMALS** vor, es erledigt zu haben.
  * Melde entweder direkt:
    > *"Dieser Vorgang ist auf der Worker Node Fox nicht möglich."*
  * **ODER** reihe den Job in die Task-Handover-Queue ein (`cluster_delegate.py queue ...`).

## Wann verwenden (Triggers)
* **Synchrone Auslagerung (`delegate-oh`):**
  * Große Datei- und Quellcode-Abgleiche (Diffs, semantische Code-Analysen über viele Dateien)
  * Schweres, mehrstufiges Reasoning oder tiefgründige Planungen
  * Voraussetzung: Outer Heaven ist online und ComfyUI frei (`VRAM < 4 GB`).
* **Asynchrone Warteschlange (`queue`):**
  * Aufgaben, die Outer Heaven erfordern, aber Outer Heaven schläft oder rendert gerade.
  * Jobs, die im Hintergrund ausgeführt werden sollen, sobald ComfyUI inaktiv ist.
* **Nicht verwenden für:**
  * Einfache Dateibefehle (`read_file`, `write_file`, einfache Bash-Befehle)
  * Schnelle Chat-Antworten und Telegram-Sprachausgaben

## Ausführung via Hermes Terminal-Tool

Aufruf immer über das `terminal`-Werkzeug:

```bash
# 1. Sofortige Auslagerung mit automatischem Fallback:
python3 /home/fox/scripts/cluster_delegate.py delegate-oh "Vergleiche die Dateien A und B und erstelle eine Liste aller Differenzen."

# 2. In die Task-Handover-Queue (/mnt/storage-beta/cluster_tasks.json) einreihen:
python3 /home/fox/scripts/cluster_delegate.py queue "Drive Asset Sync" "Lade alle neuen Modell-Assets aus Google Drive herunter und verifiziere die Hashes."

# 3. Queue-Status abfragen:
python3 /home/fox/scripts/cluster_delegate.py queue-list

# 4. Cluster- und Ressourcen-Status prüfen:
python3 /home/fox/scripts/cluster_delegate.py status
```

## Python-Modulnutzung

```python
import sys
sys.path.append("/home/fox/scripts") # bzw. ~/.hermes/skills/devops/cluster-delegate/scripts
from cluster_delegate import delegate_to_outerheaven, enqueue_task, load_cluster_tasks

# Job in Queue einreihen
task = enqueue_task(
    name="Drive Asset Sync",
    prompt="Synchronisiere Drive Assets nach /mnt/fox-assets/checkpoints"
)
print(f"Eingereiht: {task['id']}")
```
