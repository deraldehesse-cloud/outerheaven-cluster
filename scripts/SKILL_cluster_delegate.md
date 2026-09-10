---
name: cluster-delegate
description: "Cluster workload delegation between Fox and Outer Heaven."
version: 1.0.0
author: Outer Heaven Cluster
license: MIT
metadata:
  hermes:
    tags: [Cluster, Delegation, Offload, Compute, OuterHeaven, Fox]
---

# Cluster Delegate (Outer Heaven ↔ Fox)

Dieses Werkzeug steuert die bidirektionale Lastverteilung zwischen Node **Fox** (Standby-Worker & Gateway) und Node **Outer Heaven** (Heavy Compute).

## Wann verwenden (Triggers)
* **Rechenintensive Aufgaben vor der Ausführung:**
  * Große Datei- und Quellcode-Abgleiche (Diffs, semantische Code-Analysen über viele Dateien)
  * Schweres, mehrstufiges Reasoning oder tiefgründige Planungen
  * Zusammenfassungen oder Übersetzungen sehr umfangreicher Dokumente
* **Nicht verwenden für:**
  * Einfache Dateibefehle (`read_file`, `write_file`, einfache Bash-Befehle)
  * Schnelle Chat-Antworten und Telegram-Sprachausgaben
  * Lokale System- und Wartungsbefehle auf Fox

## Funktionsweise & Kriterien
1. **Prüfung:** Fox prüft vor der Ausführung autonom, ob Outer Heaven (`100.116.38.29`) erreichbar ist und ComfyUI inaktiv ist (`VRAM < 4 GB` und ComfyUI-Warteschlange leer).
2. **Frei (ComfyUI schläft / VRAM < 4 GB):** Die Rechenaufgabe wird an Outer Heavens Ollama-Instanz (`deepseek-r1:14b`) mit 8192 Kontext übermittelt. Nach Rückerhalt führt Fox ausschließlich die lokalen Dateioperationen aus.
3. **Belegt (ComfyUI aktiv / VRAM >= 4 GB / Outer Heaven offline):** Fox rechnet autonom mit seinem lokalen Modell (`hermes-3-3b-q4:latest`) weiter.
4. **Mission Control Sync:** Jeder Task wird in Echtzeit an das Dashboard (:8080) gemeldet (`⚡ DELEGATED TO OH` bzw. `🦊 DELEGATED TO FOX`).

## Ausführung via Hermes Terminal-Tool

Aufruf immer über das `terminal`-Werkzeug:

```bash
# Rechenintensive Aufgabe auslagern (mit automatischem Fallback)
python3 /home/fox/scripts/cluster_delegate.py delegate-oh "Vergleiche die Dateien A und B und erstelle eine Liste aller Differenzen und Refactoring-Schritte."

# Automatische Delegation (erkennt aktuellen Host selbst)
python3 /home/fox/scripts/cluster_delegate.py run "Erstelle einen detaillierten Entwurf für die Systemarchitektur."

# Cluster-Status und VRAM-Auslastung prüfen
python3 /home/fox/scripts/cluster_delegate.py status
```

## Python-Modulnutzung

```python
import sys
sys.path.append("/home/fox/scripts") # bzw. ~/.hermes/skills/devops/cluster-delegate/scripts
from cluster_delegate import delegate_to_outerheaven, check_outerheaven_comfyui_and_vram

# Status prüfen
is_busy, vram_gb, reason = check_outerheaven_comfyui_and_vram()

# Rechenaufgabe delegieren
result = delegate_to_outerheaven("Analysiere die Logdateien auf Anomalien.")
print(result["response"])
```
