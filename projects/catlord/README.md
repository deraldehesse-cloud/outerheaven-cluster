# 😼 Projekt Catlord

> **Attic Guy, das „Zentrum der Macht“ und die Katzen-Assassinen.**  
> Spielkonzept, Storytelling, LoRA-Trainings-Pipelines und Prototypen für das Outer Heaven Cluster.

---

## 📖 1. Übersicht & Handlung
**Catlord** erzählt die Geschichte von **Sebastian („Attic Guy“)**, der unterm Dach wohnt, das echte handwerkliche Fachwissen besitzt und ständig die absurden Pfusch- und Baustellenkatastrophen seines verpeilten Chefs **Gert Baert („Zentrum der Macht“)** ausbügeln muss. Um sich gegen Gläubiger, Mafiosi und das tägliche Chaos zu verteidigen, trainiert Sebastian heimlich seine Kater zu schlagkräftigen Ninja-Assassinen:

* 🛡️ **Elliot (Der Tank):** Massiver orange-getigerter Maine-Coon-Kater (8 kg) mit „Krallen wie Wolverine“, Spezialfähigkeiten: *Arsch-Anschlag* (Türen verbarrikadieren) und *Waschbecken-Slam*.
* ⚡ **Loco / Loki (Der Assassin):** Agiler, grau-weißer Maine-Coon-Kater, maximal wurffähig, Spezialfähigkeit: *Tail-Shade & Toss* (visuelle Tarnung und gezielter Wurf durch Attic Guy).
* 🥺 **Käch / Cash (Der Manipulator):** Besitzt den unwiderstehlichen *Kulleraugen-Boost* zur Ablenkung von Gegnern.
* 🥋 **Holly (Die Veteranin):** Disziplinierte Hintergrund-Strategin der Katzen-Truppe.

---

## 📁 2. Verzeichnisstruktur im Monorepo

```
projects/catlord/
├── README.md               # Dieses Projekt-Dokument
├── link-media.sh           # Hilfsskript zur Verknüpfung des Cluster-Medienspeichers
├── .gitignore              # Ausschluss schwerer Binär- & Mediendateien sowie media-Symlink
├── docs/                   # Spielkonzepte, Charakterprofile, Storyboards und Style-Guides
│   ├── charaktere.md       # Detaillierte Charakterübersicht (Mensch & Katze)
│   ├── gameplay.md         # Gameplay-Mechaniken, Wurf-Physik und Taktik
│   ├── geschichte.md       # Hintergrundgeschichte & Baustellen-Anekdoten
│   └── ...                 # Weitere Notizen und Gemini-Konzepte
├── src/                    # Python-Automationen, Bild-Tagging und Worker-Skripte
│   ├── caption_pipeline.py # Automatisierte Captioning-Pipeline für LoRA-Training
│   └── catlord_idle_worker.py # Idle-Worker-Skript
├── prompts/                # Generierungs-Prompts für ComfyUI & Krita
├── rea-findings/           # Reverse-Engineering-Auswertungen aus der REA-Sandbox (metal-dgear)
├── voice-notes/            # Sprachnotizen-Transkripte & Audio-Notizen
└── media/ -> /mnt/storage-beta/projects/catlord-media  (Symlink auf Cluster-Storage)
```

---

## 💾 3. Medien-Storage & NFS-Cluster-Anbindung

Große Video- und Bilddateien (Rohaufnahmen, LoRA-Trainingsdatensätze, 4K-Footage) werden **nicht** im Git-Repository versioniert, sondern liegen zentral auf Node **`outerheaven`** unter `/mnt/storage-beta/projects/catlord-media/`:

* `media/elliot/` ➔ Trainingsdatensatz Elliot
* `media/kaech/` ➔ Trainingsdatensatz Käch / Cash
* `media/raw-footage/` ➔ Alle Rohvideos (`.mp4`) und hochauflösende Fotos
* `media/references/` ➔ Design-Dokumente, Krita-Skizzen und Vorlagen
* `media/gdrive-sync/` ➔ Automatischer Sync-Spiegel des Google-Drive-Ordners `gdrive:Agenten-Drive/projekt-catlord`

### Symlink auf einem Node neu setzen:
```bash
./projects/catlord/link-media.sh
```
