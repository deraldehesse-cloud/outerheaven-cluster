# 🛰️ Outer Heaven Multi-Node AI Cluster

> **Verteiltes heterogenes KI-Cluster-Setup für lokale LLM-Inferenz, autonome Agenten und Bildgenerierung (Intel Arc A770 16GB XPU + AVX2 Worker Node via Tailscale Mesh-VPN).**

---

## 🏛️ 1. Cluster-Architektur & Hardware-Topologie

Das Cluster verbindet zwei physische Hosts und einen Administrations-Laptop über ein verschlüsseltes **Tailscale-Mesh-Netzwerk** zu einer kohärenten, ausfallsicheren KI-Infrastruktur:

```
                  +-------------------------------------------------+
                  |         LAPTOP "BIG BOSS" (Admin / Dev)         |
                  |             Tailscale IP: <BIGBOSS_IP>          |
                  +------------------------+------------------------+
                                           |
                                   (Tailscale VPN)
                                           |
         +---------------------------------+---------------------------------+
         |                                                                   |
+--------v--------------------------------+         +------------------------v-----------------------+
|  NODE 1: "OUTER HEAVEN" (On-Demand)    |         |  NODE 2: "FOX" (Always-On 24/7 Gateway)       |
|  Tailscale IP: <OUTERHEAVEN_IP>        |         |  Tailscale IP: <FOX_IP>                       |
|  LAN IP:       <OUTERHEAVEN_LAN_IP>    | <=====> |  LAN IP:       <FOX_LAN_IP>                   |
|----------------------------------------|   NFS   |-----------------------------------------------|
| • Hardware: HP Z620 Workstation        | (Storage| • Hardware: HP EliteDesk 800 G1 SFF           |
| • CPU: Dual Intel Xeon (AVX1 only!)    |  Beta)  | • CPU: Intel Core i5 (Haswell - AVX2 / FMA3)  |
| • GPU: Sparkle Intel Arc A770 16GB XPU |         | • RAM: 16 GB DDR3 + 8 GB Swap                 |
| • RAM: 48 GB DDR3 ECC                  |         | • Rollen:                                     |
| • Rollen:                              |         |   - Hermes-3 3B (Ollama Standby Engine)       |
|   - DeepSeek-R1 14B (Reasoning Engine) |         |   - Tailscale Ollama Proxy (:11434)           |
|   - ComfyUI (DreamShaper8 LCM via XPU) |         |   - Telegram Gateway & Audio Transcoding      |
|   - Mission Control Dashboard (PWA)    |         |   - 48h Auto-Reboot Handover Timer            |
|   - NFS Host (/mnt/storage-beta)       |         |                                               |
+----------------------------------------+         +-----------------------------------------------+
```

---

## ⚠️ 2. Die AVX1-Herausforderung & SIGILL-Vermeidung

### Das technische Problem
Die Primär-Workstation **outerheaven** (HP Z620) nutzt Dual Intel Xeon Prozessoren der Sandy-Bridge-EP-Generation. Diese CPUs unterstützen **ausschließlich AVX1** (kein AVX2, kein FMA3).
Moderne Machine-Learning-Laufzeiten (wie `llama.cpp`, `torchvision`, `kornia-rs` oder ONNX-Runtime) enthalten Binärcode, der für AVX2 optimiert ist. Sobald eine AI-Operation auf der CPU ausgeführt wird, stürzt der Prozess augenblicklich mit einem fatalen Signal ab:
```
SIGILL (Illegal Instruction, Core Dumped)
```

### Die Cluster-Schutzstrategie
1. **100 % VRAM-Allokation auf Arc A770:**
   Alle Modellgewichte von `deepseek-r1:14b` werden strikt vollständig in den 16 GB VRAM der Sparkle Intel Arc A770 geladen (`num_gpu: 999`, kein CPU-Offloading).
2. **Kornia-RS Eliminierung in ComfyUI:**
   Rust-basierte AVX2-Erweiterungen (`kornia-rs`) wurden in der virtuellen Umgebung deinstalliert und durch reine Python/PyTorch-Operationen ersetzt.
3. **Automatische Workload-Delegation an Node Fox:**
   Sobald ComfyUI den Grafikspeicher für Bildgenerierungen beansprucht, delegiert Hermes anfallende Text- und Hilfsaufgaben transparent über `cluster_delegate.py` an Node **fox**, da dessen Haswell-CPU nativen AVX2-Support besitzt.

---

## 🔄 3. Workload-Delegation & Graceful Handover

### A. Intelligente Delegation (`cluster_delegate.py`)
- Prüft in Echtzeit die ComfyUI-Queue (`http://127.0.0.1:8188/queue`) und den freien VRAM der Arc A770.
- **ComfyUI Idle & VRAM frei:** Anfragen laufen lokal auf Outer Heaven mit `deepseek-r1:14b`.
- **ComfyUI aktiv:** Anfragen werden per Tailscale-API an Fox mit `hermes-3-3b-q4:latest` geleitet.

### B. Pre-Shutdown & Reboot Handover
Um zu verhindern, dass Tasks an Node Fox gesendet werden, während Fox einen geplanten Systemneustart (z. B. den 48h-Wartungsreboot) durchführt:
1. **Hook auf Fox (`cluster-pre-shutdown.sh`):**
   Signalisiert Outer Heaven vor dem Herunterfahren via SSH (`touch /tmp/fox_is_rebooting`) und REST-API (`POST /api/handover`).
2. **Schutz auf Outer Heaven:**
   `cluster_delegate.py` und `hermes_telegram_service.py` prüfen das Flag. Bei gesetztem Flag wird die Delegation an Fox blockiert und Aufgaben werden lokal abgearbeitet.
3. **Boot-Hook auf Fox (`cluster-boot-notify.sh`):**
   Meldet nach dem Hochfahren Entwarnung (`rm -f /tmp/fox_is_rebooting; touch /tmp/fox_online`).

---

## 🛡️ 4. Mission Control Dashboard (PWA)

Das Web-Dashboard dient als zentrale Leitstelle zur Überwachung und Steuerung beider Knoten:

- **Striktes Host-Binding (Zero-0.0.0.0 Policy):**
  Bindet **ausschließlich** an `127.0.0.1:8080` und die Tailscale-IP `<OUTERHEAVEN_IP>:8080`. Ein offenes Binding an `0.0.0.0` ist architektonisch unterbunden.
- **Authentifizierung:**
  Geschützt durch Session-Cookie, HTTP-Basic-Auth und Inter-Agent API-Key (`X-Cluster-Key`).
- **Multi-Node Telemetrie:**
  Echtzeit-Anzeige von Arc A770 VRAM, CPU/RAM, Ollama-Modellen und systemd-Dienststatus beider Knoten.
- **Dual-Agent Chat mit serverseitigem Filter:**
  WebSocket-Streaming mit Umschalter zwischen `Outer Heaven (DeepSeek 14B)` und `Fox (Hermes 3B)`. Die `<think>...</think>`-Reasoning-Tags von DeepSeek R1 werden serverseitig in Echtzeit gefiltert.
- **Sprachausgabe (TTS):**
  Zuschaltbare deutsche Sprachausgabe via Browser Web Speech API.
- **ComfyUI-Bridge:**
  Direkte Bildgenerierung und Galerie der letzten Outputs.
- **PWA:**
  Vollständig installierbar als Progressive Web App (Manifest & Service Worker).

---

## 📂 5. Repository-Struktur

```
.
├── .env.example                         # Umgebungsvariablen-Vorlage
├── config.yaml.example                  # Hermes Agent Konfigurations-Template
├── .gitignore                           # Strenger Ausschluss sensibler Daten
├── README.md                            # Diese Dokumentation
│
├── scripts/
│   ├── cluster_delegate.py              # Intelligentes Multi-Node Routing & Handover-Schutz
│   ├── watch-ollama-model.sh            # Ollama Model Watcher & VRAM-Flush Synchronizer
│   ├── cluster-pre-shutdown.sh          # Fox Pre-Shutdown Signal Hook
│   ├── cluster-boot-notify.sh           # Fox Boot Recovery Hook
│   ├── hermes_telegram_service.py       # Telegram Bot Service (Bigbossheaven_bot) mit ComfyUI-Bridge
│   ├── verify-cluster.sh                # Ganzheitlicher Cluster-Health-Check
│   ├── google_calendar.py               # Google Calendar Integration via Service Account
│   └── google_drive.py                  # Google Drive Integration via Service Account
│
├── mission-control/
│   ├── server.py                        # Aiohttp Async Backend (Dual TCPSite Binding)
│   ├── templates/
│   │   ├── index.html                   # Sci-Fi Terminal UI mit Tailwind, Chat, Gauges & TTS
│   │   └── login.html                   # Authentifizierungs-Terminal
│   └── static/
│       ├── manifest.json                # PWA Web App Manifest
│       ├── sw.js                        # PWA Service Worker
│       └── icon.svg                     # Vektor-Cluster-Icon
│
└── systemd/
    ├── mission-control.service          # User-Dienst für das Dashboard
    ├── hermes-telegram.service          # User-Dienst für den Telegram-Bot
    ├── comfyui.service                  # User-Dienst für ComfyUI auf Arc A770
    ├── ollama-model-watcher.service     # System-Dienst für VRAM-Schutz & Modellwechsel
    ├── cluster-shutdown-notify.service  # Handover-Hook vor Reboot auf Fox
    ├── cluster-boot-notify.service      # Handover-Hook nach Boot auf Fox
    └── ollama-tailscale-proxy.service   # Tailscale-Proxy für Ollama auf Fox
```

---

## 🚀 6. Schnellstart & Inbetriebnahme

### 1. Umgebungsvariablen anlegen
Kopiere die Vorlagen und trage deine Zugangsdaten ein:
```bash
cp .env.example .env
cp config.yaml.example config.yaml
```

Erstelle die benötigten Secret-Dateien mit Rechten `chmod 600`:
```bash
mkdir -p /path/to/secrets
chmod 700 /path/to/secrets

echo "<YOUR_TELEGRAM_BOT_TOKEN>" > /path/to/secrets/telegram_outerheaven.token
echo "<DASHBOARD_PASSWORD>" > /path/to/secrets/dashboard_password.txt
openssl rand -hex 32 > /path/to/secrets/cluster_api.key

chmod 600 /path/to/secrets/*
```

### 2. Systemd-Dienste aktivieren
Auf **Outer Heaven**:
```bash
systemctl --user enable --now comfyui.service
systemctl --user enable --now hermes-telegram.service
systemctl --user enable --now mission-control.service
sudo systemctl enable --now ollama-model-watcher.service
```

Auf **Fox**:
```bash
systemctl --user enable --now ollama-tailscale-proxy.service
systemctl --user enable --now cluster-shutdown-notify.service
systemctl --user enable --now cluster-boot-notify.service
```

### 3. Cluster-Diagnose ausführen
Von der Administrations-Station:
```bash
bash scripts/verify-cluster.sh
```

---

## 🔒 7. Sicherheits- und Lizenzhinweise

- **Lizenz:** MIT License — Open Source für Forschungs- und Heimanwendungszwecke.
- **Sicherheits-Audit:** Sämtliche API-Schlüssel, Tokens und Passwörter müssen strikt außerhalb des Git-Baums in geschützten Secret-Dateien liegen. Prüfe vor jedem Commit, dass `.gitignore` aktiv ist.
