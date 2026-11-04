#!/usr/bin/env python3
"""
voice_tts.py - Text-to-Speech Helper-Skript für Outer Heaven Hermes Agent.
Basiert auf piper-tts und de_DE-thorsten-medium.
Nimmt Text als Argument oder via stdin entgegen und generiert eine Audiodatei (.ogg oder .wav).
"""

import os
import sys
import re
import argparse
import subprocess
import tempfile
from pathlib import Path

DEFAULT_MODEL = os.getenv("PIPER_MODEL", "/home/outerheaven/models/piper/de_DE-kerstin-low.onnx")
DEFAULT_OUTPUT = "/tmp/hermes_response.ogg"
PIPER_BIN = "/home/outerheaven/.hermes/.venv/bin/piper"


def clean_text_for_tts(text: str) -> str:
    """Bereinigt den Text für eine flüssige und verständliche Sprachausgabe."""
    if not text:
        return ""
    # Think-Tags entfernen (DeepSeek R1 etc.)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    # Code-Blöcke ersetzen
    text = re.sub(r"```[\w]*\n(.*?)```", " Codeblock ausgelassen. ", text, flags=re.DOTALL)
    # Inline Code
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Markdown Links [text](url) -> text
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    # URLs entfernen
    text = re.sub(r"https?://\S+", "", text)
    # Markdown Formatierungen entfernen (*, _, ~, #, >)
    text = re.sub(r"[*_~#>]+", " ", text)
    # Mehrfache Leerzeichen und Zeilenumbrüche reduzieren
    text = re.sub(r"\s+", " ", text).strip()
    return text


def synthesize(text: str, output_path: str, model_path: str = DEFAULT_MODEL) -> str:
    cleaned = clean_text_for_tts(text)
    if not cleaned:
        raise ValueError("Kein sprechbarer Text übergeben.")

    if not os.path.isfile(model_path):
        piper_dir = Path("/home/outerheaven/models/piper")
        if piper_dir.is_dir():
            de_models = list(piper_dir.glob("de_DE-*.onnx"))
            if de_models:
                model_path = str(de_models[0])
        if not os.path.isfile(model_path):
            raise FileNotFoundError(f"Piper Modell nicht gefunden: {model_path}")

    # Piper Binary ermitteln
    piper_cmd = PIPER_BIN if os.path.isfile(PIPER_BIN) else "piper"

    out_file = Path(output_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)

    is_ogg = out_file.suffix.lower() == ".ogg"

    if is_ogg:
        # Piper nach temporärer WAV rendern, dann via ffmpeg nach OGG Opus konvertieren
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
            tmp_wav_path = tmp_wav.name

        try:
            piper_proc = subprocess.run(
                [piper_cmd, "-m", model_path, "-f", tmp_wav_path],
                input=cleaned.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True
            )

            # ffmpeg Konvertierung zu Telegram-kompatiblem OGG Opus
            ffmpeg_cmd = [
                "ffmpeg", "-y", "-i", tmp_wav_path,
                "-c:a", "libopus", "-b:a", "64k",
                "-v", "error",
                str(out_file)
            ]
            subprocess.run(ffmpeg_cmd, check=True)

        finally:
            if os.path.exists(tmp_wav_path):
                os.remove(tmp_wav_path)
    else:
        # Direkt als WAV ausgeben
        subprocess.run(
            [piper_cmd, "-m", model_path, "-f", str(out_file)],
            input=cleaned.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True
        )

    return str(out_file)


def main():
    parser = argparse.ArgumentParser(description="Erstellt Sprachausgabe via piper-tts.")
    parser.add_argument("text", nargs="?", default=None, help="Zu sprechender Text (optional wenn via stdin übergeben)")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT, help=f"Ausgabedatei (.ogg oder .wav, Standard: {DEFAULT_OUTPUT})")
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL, help="Pfad zum Piper .onnx Modell")

    args = parser.parse_args()

    input_text = args.text
    if not input_text:
        if not sys.stdin.isatty():
            input_text = sys.stdin.read().strip()

    if not input_text:
        sys.stderr.write("Fehler: Kein Text zum Sprechen angegeben.\n")
        sys.exit(1)

    try:
        out_file = synthesize(input_text, args.output, args.model)
        print(out_file)
        sys.exit(0)
    except Exception as e:
        sys.stderr.write(f"Fehler bei TTS-Generierung: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
