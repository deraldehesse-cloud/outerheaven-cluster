#!/usr/bin/env python3
"""
audio_haptics.py - Autonomous Haptic-Audio Engine for Lovense LAN Closed-Loop.

Analyzes Edge-TTS generated voice audio (.ogg Opus, .mp3, .wav) via FFT & RMS speech tracking,
smooths transitions via dual-rate Attack/Decay envelope filtering, and streams synchronized
vibrations (0-20) in real-time to the Lovense LAN Bridge so toys pulse to Haven's voice.
"""

import os
import sys
import time
import math
import wave
import json
import shutil
import logging
import argparse
import subprocess
import threading
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

import numpy as np
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [audio_haptics]: %(message)s")
logger = logging.getLogger("audio_haptics")

# Default Lovense LAN Bridge endpoint
DEFAULT_LOVENSE_IP = os.environ.get("LOVENSE_IP", "192.168.188.34")
DEFAULT_LOVENSE_PORT = int(os.environ.get("LOVENSE_PORT", 20010))
DEFAULT_COMMAND_URL = f"http://{DEFAULT_LOVENSE_IP}:{DEFAULT_LOVENSE_PORT}/command"

# Audio & Envelope Parameters
SAMPLE_RATE = 16000          # 16kHz standard mono speech sample rate
FRAME_DURATION_MS = 50       # 50ms window length
HOP_DURATION_MS = 25         # 25ms hop step (40 fps)
ATTACK_ALPHA = 0.65          # Fast attack for consonant/vowel onset
DECAY_ALPHA = 0.18           # Smooth exponential decay to eliminate motor stutter
NOISE_GATE_THRESHOLD = 0.04  # Below this amplitude is considered silence/pause


def get_ffmpeg_bin() -> str:
    """Finds available ffmpeg binary."""
    for candidate in [
        shutil.which("ffmpeg"),
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/home/fox/.local/bin/ffmpeg",
        "/home/outerheaven/.hermes/.venv/bin/ffmpeg",
    ]:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return "ffmpeg"


def decode_audio_to_pcm(audio_path: str, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """
    Decodes audio file (.ogg, .mp3, .wav) to 16kHz 16-bit mono float32 numpy array [-1.0, 1.0].
    """
    p = Path(audio_path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    ffmpeg_bin = get_ffmpeg_bin()
    cmd = [
        ffmpeg_bin,
        "-v", "error",
        "-i", str(p),
        "-f", "s16le",
        "-acodec", "pcm_s16le",
        "-ac", "1",
        "-ar", str(sample_rate),
        "pipe:1"
    ]

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        raw_pcm, err = proc.communicate(timeout=15)
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg decode error: {err.decode('utf-8', errors='ignore')}")

        samples = np.frombuffer(raw_pcm, dtype=np.int16).astype(np.float32) / 32768.0
        return samples

    except Exception as e:
        logger.error(f"Failed to decode audio {p.name}: {e}")
        # Return synthetic sine wave for fallback/testing
        t = np.linspace(0, 2.0, int(sample_rate * 2.0), endpoint=False)
        return (0.4 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def extract_speech_envelope(
    samples: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    frame_ms: int = FRAME_DURATION_MS,
    hop_ms: int = HOP_DURATION_MS
) -> List[Tuple[float, float]]:
    """
    Extracts smoothed speech envelope over time:
    Returns list of (timestamp_sec, normalized_energy_0_to_1).
    """
    if len(samples) == 0:
        return []

    frame_size = int(sample_rate * (frame_ms / 1000.0))
    hop_size = int(sample_rate * (hop_ms / 1000.0))

    envelope = []
    current_env = 0.0

    # Speech frequency band indices for 16kHz FFT (120Hz to 3400Hz)
    freqs = np.fft.rfftfreq(frame_size, d=1.0 / sample_rate)
    voice_band = (freqs >= 120) & (freqs <= 3400)

    # Frame extraction loop
    for start in range(0, len(samples) - frame_size + 1, hop_size):
        frame = samples[start:start + frame_size]
        t_sec = start / sample_rate

        # 1. RMS Energy
        rms = np.sqrt(np.mean(frame ** 2) + 1e-12)

        # 2. FFT Spectral Energy in vocal range
        windowed = frame * np.hanning(frame_size)
        fft_mag = np.abs(np.fft.rfft(windowed))
        vocal_energy = np.mean(fft_mag[voice_band]) if np.any(voice_band) else rms

        # Combined raw metric
        raw_energy = 0.65 * rms + 0.35 * vocal_energy
        raw_energy = min(1.0, raw_energy * 3.5)  # Scale up conversational speech dynamic range

        # 3. Dual-rate Attack/Decay Exponential Smoothing Filter
        if raw_energy > current_env:
            # Fast attack
            current_env = ATTACK_ALPHA * raw_energy + (1.0 - ATTACK_ALPHA) * current_env
        else:
            # Smooth decay to prevent motor stutter
            current_env = DECAY_ALPHA * raw_energy + (1.0 - DECAY_ALPHA) * current_env

        envelope.append((round(t_sec, 3), float(np.clip(current_env, 0.0, 1.0))))

    return envelope


def map_envelope_to_vibration(
    envelope: List[Tuple[float, float]],
    noise_gate: float = NOISE_GATE_THRESHOLD,
    max_level: int = 20
) -> List[Tuple[float, int]]:
    """
    Maps normalized envelope [0.0, 1.0] to discrete Lovense vibration steps [0, 20].
    Applies non-linear dynamic range curve for responsive sensual feedback.
    """
    vibration_track = []
    for t_sec, val in envelope:
        if val < noise_gate:
            level = 0
        else:
            # Non-linear perceptual scaling
            norm_val = (val - noise_gate) / (1.0 - noise_gate)
            scaled = math.pow(norm_val, 0.85) * max_level
            level = int(round(np.clip(scaled, 1, max_level)))

        vibration_track.append((t_sec, level))

    return vibration_track


def render_ascii_waveform(track: List[Tuple[float, int]], max_samples: int = 40) -> str:
    """Renders a text visualization of the haptic waveform."""
    if not track:
        return "[Empty Track]"

    # Subsample for display
    step = max(1, len(track) // max_samples)
    sub = track[::step]

    bars = [" ", " ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    res = []
    for _, lvl in sub:
        idx = min(len(bars) - 1, int((lvl / 20.0) * (len(bars) - 1)))
        res.append(bars[idx])

    return "".join(res)


class HapticAudioPlayer:
    """
    Asynchronous closed-loop player that transmits synchronized vibration
    packets to the Lovense LAN bridge during speech audio playback.
    """

    def __init__(self, command_url: str = DEFAULT_COMMAND_URL):
        self.command_url = command_url
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def stop(self):
        """Immediately stops the player and zeroes toy vibration."""
        self._stop_event.set()
        try:
            requests.post(
                self.command_url,
                json={"command": "Function", "action": "Vibrate:0", "timeSec": 0, "apiVer": 1},
                timeout=1.0
            )
        except Exception:
            pass

    def play_synced_haptics(
        self,
        audio_path: str,
        simulate: bool = False,
        min_change_delta: int = 1,
        throttle_sec: float = 0.09
    ) -> List[Tuple[float, int]]:
        """
        Processes audio and streams timed vibration commands in sync with playback.
        Can run simulated or live over LAN.
        """
        samples = decode_audio_to_pcm(audio_path)
        envelope = extract_speech_envelope(samples)
        track = map_envelope_to_vibration(envelope)

        duration = len(samples) / SAMPLE_RATE
        logger.info(f"Audio '{Path(audio_path).name}' duration: {duration:.2f}s, frames: {len(track)}")

        if simulate:
            ascii_wave = render_ascii_waveform(track)
            logger.info(f"Haptic Waveform: [{ascii_wave}]")
            return track

        # Live transmission loop
        self._stop_event.clear()

        def _worker():
            start_wall = time.time()
            last_level = -1
            last_sent_time = 0.0

            for t_target, level in track:
                if self._stop_event.is_set():
                    break

                # Sleep until packet timestamp
                now_elapsed = time.time() - start_wall
                sleep_needed = t_target - now_elapsed
                if sleep_needed > 0:
                    time.sleep(sleep_needed)

                # Throttle transmission rate to avoid Bluetooth packet congestion
                now = time.time()
                time_since_last = now - last_sent_time
                level_diff = abs(level - last_level)

                if (level_diff >= min_change_delta and time_since_last >= throttle_sec) or (level == 0 and last_level > 0):
                    payload = {
                        "command": "Function",
                        "action": f"Vibrate:{level}",
                        "timeSec": 0.2,
                        "apiVer": 1
                    }
                    try:
                        requests.post(self.command_url, json=payload, timeout=0.8)
                        last_level = level
                        last_sent_time = now
                    except Exception as tx_err:
                        logger.debug(f"Lovense transmission skipped: {tx_err}")

            # Ensure toy stops at the end of speech
            try:
                requests.post(
                    self.command_url,
                    json={"command": "Function", "action": "Vibrate:0", "timeSec": 0, "apiVer": 1},
                    timeout=1.0
                )
            except Exception:
                pass

        self._thread = threading.Thread(target=_worker, daemon=True)
        self._thread.start()
        return track


# Global singleton instance
haptic_engine = HapticAudioPlayer()


def play_voice_haptics_background(audio_path: str, simulate: bool = False):
    """Spawns background haptic streaming thread for voice notes."""
    try:
        haptic_engine.play_synced_haptics(audio_path, simulate=simulate)
    except Exception as e:
        logger.warning(f"Background audio haptics error: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Autonomous Haptic-Audio Engine for Lovense")
    parser.add_argument("--audio", default=None, help="Path to audio file (.ogg, .mp3, .wav)")
    parser.add_argument("--simulate", action="store_true", help="Run in simulation mode without sending LAN commands")
    parser.add_argument("--test", action="store_true", help="Generate synthetic speech tone and test envelope/mapping")
    parser.add_argument("--url", default=DEFAULT_COMMAND_URL, help="Lovense LAN Command URL")
    args = parser.parse_args()

    if args.test or not args.audio:
        print("=== Testing Haptic-Audio Engine with Synthetic Speech Tone ===")
        # Generate 2.5 second synthetic speech-like pulse
        sr = 16000
        dur = 2.5
        t = np.linspace(0, dur, int(sr * dur), endpoint=False)
        # 3 distinct vocal pulses with pauses
        pulse1 = np.sin(2 * np.pi * 240 * t) * (np.sin(2 * np.pi * 2.0 * t) > 0.1)
        pulse2 = 0.5 * np.sin(2 * np.pi * 480 * t) * (np.sin(2 * np.pi * 1.5 * t) > 0.3)
        synth = ((pulse1 + pulse2) * 0.4).astype(np.float32)

        envelope = extract_speech_envelope(synth, sample_rate=sr)
        track = map_envelope_to_vibration(envelope)
        waveform = render_ascii_waveform(track)

        print(f"Synthesized duration: {dur}s | Frames: {len(track)}")
        print(f"Vibration levels: min={min(lvl for _, lvl in track)}, max={max(lvl for _, lvl in track)}")
        print(f"Haptic Rhythm: [{waveform}]")
        print("\nSample Timestamps & Vibrations (first 10 frames):")
        for ts, lvl in track[:10]:
            print(f"  t={ts:0.3f}s -> Level {lvl:2d} / 20")

    else:
        player = HapticAudioPlayer(command_url=args.url)
        track = player.play_synced_haptics(args.audio, simulate=args.simulate)
        waveform = render_ascii_waveform(track)
        print(f"\nHaptic Analysis Complete: [{waveform}]")
        print(f"Total Frames: {len(track)}")
