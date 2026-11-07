#!/bin/bash
set -eo pipefail

DROPZONE_DIR="/mnt/storage-fast/rea-sandbox/workspace/catlord-dropzone"
OUTPUT_BASE="/mnt/storage-fast/rea-sandbox/output/catlord"
COMPOSE_FILE="/mnt/storage-fast/rea-sandbox/docker-compose.yml"
MODEL="deepseek-coder:6.7b-instruct-q4_K_M"
OLLAMA_URL="http://localhost:11434/api/generate"

mkdir -p "$DROPZONE_DIR" "$OUTPUT_BASE"

shopt -s nullglob
APKS=("$DROPZONE_DIR"/*.apk)

echo "=========================================================="
echo "    Catlord APK Decompilation & LLM Analysis Pipeline    "
echo "=========================================================="

if [ ${#APKS[@]} -eq 0 ]; then
    echo "Keine .apk-Dateien in $DROPZONE_DIR gefunden."
    echo ""
    read -t 10 -p "Beende in 10s oder drücke [Enter]..." || true
    exit 0
fi

echo "Gefundene APKs: ${#APKS[@]}"

for apk in "${APKS[@]}"; do
    apk_file="$(basename "$apk")"
    pkg_name="${apk_file%.apk}"
    out_dir="$OUTPUT_BASE/$pkg_name"
    context_file="$out_dir/llm_analysis_context.md"
    findings_file="$out_dir/llm_findings.md"

    mkdir -p "$out_dir"
    echo ""
    echo "----------------------------------------------------------"
    echo "Verarbeite APK: $apk_file"
    echo "Zielordner:     $out_dir"
    echo "----------------------------------------------------------"

    # 1. Dekompilieren mit Docker-JADX (Relative Container-Pfade)
    echo "[1/4] Starte JADX-Dekompilierung (Docker)..."
    docker compose -f "$COMPOSE_FILE" run --rm rea-sandbox jadx \
        -d "/output/catlord/${pkg_name}" \
        "/workspace/catlord-dropzone/$(basename "$apk")" \
        --no-imports --show-bad-code || echo "Warnung: JADX beendete mit Fehlern, fahre dennoch mit extrahierten Quellen fort..."

    echo "[2/4] Extrahiere relevante Klassen (Savegame, Netzwerk, Crypto)..."
    sources_dir="$out_dir/sources"

    save_files=""
    net_files=""
    crypto_files=""
    code_snippets=""

    if [ -d "$sources_dir" ]; then
        # Zuerst anwendungsspezifischen Code priorisieren (Standard-Frameworks ausschließen)
        save_files=$(find "$sources_dir" -type f \
            ! -path "*/androidx/*" ! -path "*/android/*" ! -path "*/kotlin/*" ! -path "*/kotlinx/*" ! -path "*/com/google/*" ! -path "*/org/intellij/*" ! -path "*/org/jetbrains/*" \
            \( -iname "*save*" -o -iname "*storage*" -o -iname "*pref*" -o -iname "*database*" -o -iname "*player*" -o -iname "*inventory*" -o -iname "*game*" -o -iname "*state*" \) 2>/dev/null | head -n 20 || true)

        if [ -z "$save_files" ]; then
            save_files=$(find "$sources_dir" -type f \( -iname "*save*" -o -iname "*storage*" -o -iname "*pref*" -o -iname "*database*" \) 2>/dev/null | head -n 15 || true)
        fi

        net_files=$(find "$sources_dir" -type f \
            ! -path "*/androidx/*" ! -path "*/android/*" ! -path "*/kotlin/*" ! -path "*/kotlinx/*" ! -path "*/com/google/*" \
            \( -iname "*http*" -o -iname "*api*" -o -iname "*client*" -o -iname "*network*" -o -iname "*socket*" -o -iname "*url*" \) 2>/dev/null | head -n 20 || true)

        crypto_files=$(find "$sources_dir" -type f \
            ! -path "*/androidx/*" ! -path "*/android/*" ! -path "*/kotlin/*" ! -path "*/kotlinx/*" ! -path "*/com/google/*" \
            \( -iname "*crypt*" -o -iname "*cipher*" -o -iname "*token*" -o -iname "*auth*" -o -iname "*secret*" -o -iname "*hash*" \) 2>/dev/null | head -n 20 || true)

        # Wichtigste Kandidaten für Code-Snippets (max 2 Dateien, je bis zu 35 bereinigte Zeilen)
        candidate_files=$(echo -e "${save_files}\n${crypto_files}\n${net_files}" | grep -v '^$' | head -n 2 || true)
        while IFS= read -r cfile; do
            if [ -f "$cfile" ]; then
                rel_path="${cfile#$out_dir/}"
                code_snippets+=$'\n\n'
                code_snippets+="### Datei: $rel_path"$'\n'
                code_snippets+='```java'$'\n'
                code_snippets+=$(grep -v '@kotlin.Metadata' "$cfile" | head -n 35)
                code_snippets+=$'\n```'
            fi
        done <<< "$candidate_files"
    fi

    # Kontext-Datei erstellen
    cat << MDEOF > "$context_file"
# APK Analyse-Kontext: $pkg_name

- **APK-Datei:** \`$apk_file\`
- **Dateigröße:** $(ls -lh "$apk" | awk '{print $5}')
- **Datum:** $(date -Iseconds)
- **Ausgabe:** \`$out_dir\`

---

## 1. Identifizierte Persistenz- & Savegame-Klassen
\`\`\`
${save_files:-Keine spezifischen Savegame-Dateien gefunden}
\`\`\`

## 2. Identifizierte Netzwerk- & API-Klassen
\`\`\`
${net_files:-Keine spezifischen Netzwerk-Dateien gefunden}
\`\`\`

## 3. Identifizierte Krypto- & Authentifizierungs-Klassen
\`\`\`
${crypto_files:-Keine spezifischen Krypto-Dateien gefunden}
\`\`\`

---

## 4. Relevante Quellcode-Auszüge
${code_snippets:-Keine Quellcode-Auszüge verfügbar.}
MDEOF

    echo "Kontext gesichert unter: $context_file"

    # 3. LLM Analyse via Ollama REST API mit jq Payload & 60s Timeout
    echo "[3/4] Sende Auswertung an Ollama ($MODEL)..."
    prompt_text="Du bist ein Senior Android Reverse-Engineering-Experte.
Analysiere die folgenden dekompilierten Klassen der APK '${pkg_name}':

${code_snippets}

Fasse in 3 prägnanten Punkten auf Deutsch zusammen:
1. Spielvariablen & Datenpersistenz (Score, State, Speicherung)
2. Netzwerk / Telemetrie / Krypto
3. Modding-Bewertung (Schwierigkeit & Modding-Potenzial)"

    # JSON-Payload zwingend über jq erzeugen
    json_payload=$(jq -n \
        --arg model "$MODEL" \
        --arg prompt "$prompt_text" \
        '{model: $model, prompt: $prompt, stream: false, options: {num_predict: 140, temperature: 0.2}}')

    # REST-Call mit 60 Sekunden Timeout
    ollama_output=""
    set +e
    ollama_output=$(curl -s --max-time 60 -X POST "$OLLAMA_URL" \
        -H "Content-Type: application/json" \
        -d "$json_payload")
    curl_exit=$?
    set -e

    echo "[4/4] Speichere LLM-Ergebnisse..."
    if [ $curl_exit -ne 0 ] || [ -z "$ollama_output" ]; then
        echo "WARNUNG: Ollama-Anfrage fehlgeschlagen oder Timeout (>60s, Exit-Code: $curl_exit)!"
        cat << ERREOF > "$findings_file"
# LLM Analyse für $pkg_name

> [!WARNING]
> Die Inferenz via Ollama ($MODEL) konnte nicht innerhalb des 60s Timeouts abgeschlossen werden oder der Dienst war nicht erreichbar (Exit-Code: $curl_exit).

Die Rohdaten und extrahierten Codefragmente stehen in [llm_analysis_context.md](./llm_analysis_context.md) bereit.
ERREOF
    else
        findings=$(echo "$ollama_output" | jq -r '.response // empty')
        if [ -n "$findings" ]; then
            cat << RESEOF > "$findings_file"
# LLM Reverse Engineering Auswertung: $pkg_name

- **Modell:** \`$MODEL\`
- **Zeitpunkt:** $(date -Iseconds)
- **Status:** Erfolgreich analysiert

---

$findings
RESEOF
            echo "Erfolgreich ausgewertet und gespeichert in: $findings_file"
        else
            echo "WARNUNG: Antwort erhalten, aber .response ist leer."
            echo "$ollama_output" > "$findings_file"
        fi
    fi

done

echo ""
echo "=========================================================="
echo "    Alle APKs erfolgreich verarbeitet!                    "
echo "=========================================================="
read -t 15 -p "Drücke [Enter] zum Beenden..." || true
