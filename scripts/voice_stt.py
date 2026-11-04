#!/usr/bin/env python3
"""
voice_stt.py - Leichtgewichtiges Speech-to-Text Helper-Skript für Outer Heaven.
Basiert auf faster-whisper (CPU / int8 / float32).
Nimmt einen Pfad zu einer Audiodatei (.ogg, .wav, .mp3) entgegen und gibt den transkribierten Text auf stdout aus.
"""

import os
import sys
import argparse
import logging

# Standard-Logs auf WARNING beschränken, damit stdout für den transkribierten Text sauber bleibt
logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")


def transcribe(audio_path: str, model_size: str = "base", language: str = "de") -> str:
    if not os.path.isfile(audio_path):
        raise FileNotFoundError(f"Audiodatei nicht gefunden: {audio_path}")

    from faster_whisper import WhisperModel

    # int8 für schnelle Inferenz auf CPU versuchen, Fallback auf float32
    try:
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
    except Exception as e:
        sys.stderr.write(f"Hinweis: Fallback von int8 auf float32 ({e})\n")
        model = WhisperModel(model_size, device="cpu", compute_type="float32")

    lang = None if language.lower() in ("auto", "none", "") else language
    segments, info = model.transcribe(
        audio_path,
        language=lang,
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500)
    )

    parts = [s.text.strip() for s in segments if s.text and s.text.strip()]
    return " ".join(parts).strip()


def main():
    parser = argparse.ArgumentParser(description="Transkribiert eine Audiodatei via faster-whisper.")
    parser.add_argument("audio_path", help="Pfad zur Audiodatei (.ogg, .wav, .mp3, etc.)")
    parser.add_argument("--model", default=os.getenv("WHISPER_MODEL", "base"), help="Modellgröße (tiny, base, small, medium)")
    parser.add_argument("--language", default=os.getenv("WHISPER_LANGUAGE", "de"), help="Sprachcode (de, en, auto)")

    args = parser.parse_args()

    try:
        text = transcribe(args.audio_path, model_size=args.model, language=args.language)
        if text:
            print(text)
        sys.exit(0)
    except Exception as e:
        sys.stderr.write(f"Fehler bei STT-Transkription: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
