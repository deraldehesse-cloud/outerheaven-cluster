# Metal Rex (Media Center & Kiosk Node)

- **Host:** `rex` (`metal-rex`)
- **Hardware:** Lenovo ThinkCentre M93p Tiny
- **Tailscale-IP:** `100.79.101.50`
- **User:** `metal-rex`
- **Rolle:** Kodi Media Center, Chromium Kiosk Client & NFS Media Consumer

## Struktur
- `scripts/`: Start- und Kiosk-Skripte (z.B. Chromium Kiosk ZDF Mediathek, Kodi Web Launchers)
- `kodi/`: Kodi Userdata Konfiguration (`advancedsettings.xml`, `sources.xml`, `favourites.xml`)
- `systemd/`: Systemd Mount- und Service-Units (z.B. `mnt-nas_serien.mount` fuer NAS `deraldehesse`)

## NFS Mounts (NAS deraldehesse: 100.127.247.117)
- `/mnt/nas_filme` -> `100.127.247.117:/mnt/storage_sde1/Filme`
- `/mnt/nas_serien` -> `100.127.247.117:/mnt/storage_sdd1/Serien`
- `/mnt/nas_musik` -> `100.127.247.117:/mnt/storage_sdd1/Musik`
