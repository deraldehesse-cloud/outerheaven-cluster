# 🎙️ Voice Notes & Audio-Transkriptionen // Catlord

In diesem Verzeichnis werden Sprachnotizen, Storyboard-Diktate, Dialog-Entwürfe und Transkriptionen für das Catlord-Projekt abgelegt.

---

## 📋 Richtlinien & Speichermanagement
1. **Transkripte & Notizen (`.md`, `.txt`, `.json`):**
   * Werden direkt in diesem Ordner abgelegt und im Git-Repository versioniert.
2. **Schwere Audio-Rohdateien (`.ogg`, `.wav`, `.mp3`, `.m4a`):**
   * Gehören auf den Cluster-Speicher: `/mnt/storage-beta/projects/catlord-media/raw-footage/audio/` (bzw. über den Symlink `../media/raw-footage/audio/`).
   * Werden durch `.gitignore` strikt vom Git-Tracking ausgeschlossen, um das Repository schlank zu halten.
