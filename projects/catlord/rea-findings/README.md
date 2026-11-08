# 🔍 REA Findings (Reverse Engineering & App-Analyse) // Catlord

In diesem Verzeichnis werden Auswertungen, Dekompilierungsberichte und LLM-gestützte Code-Analysen zu Catlord-Prototypen und Referenz-Apps aus der **REA-Sandbox** auf Knoten **`metal-dgear`** (`100.96.88.84`) gesammelt.

---

## 🛠️ Sandbox-Quelle & Runner
* **Host:** `metal-dgear` (Lenovo ThinkCentre M910q)
* **Pfad auf Host:** `/mnt/storage-fast/rea-sandbox/output/catlord/`
* **Docker Environment:** `rea-sandbox` mit `jadx`, `apktool`, `radare2` und lokalen Inferenz-Skripten.

---

## 📄 Vorhandene Berichte
* [`catlord_test_llm_findings.md`](./catlord_test_llm_findings.md): Automatische LLM-Code-Analyse des Prototyps `Catlord-Test` (Fokus auf State, Game-Loop & Spielvariablen).
