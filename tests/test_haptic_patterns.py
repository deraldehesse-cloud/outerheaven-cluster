#!/usr/bin/env python3
"""
Unit-Tests für die überarbeitete Megan Studio Haptik-Engine:
- Timing- & Pacing-Parameter (MIN_FRAME_INTERVAL = 0.2, BLE_COMMAND_THROTTLE = 0.08)
- Dreieckswellen-Generatoren (triangle_wave, generate_triangle_wave)
- Rampen-Generatoren (ramp, generate_ramp)
- Pattern-Presets (low, med, high, crescendo, decrescendo)
- Not-Aus & Fail-Safe bei Pufferunterlauf (> 1.0s) und Client-Disconnect
"""

import sys
import asyncio
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "services" / "megan-studio"))
sys.path.insert(0, "/home/bigboss/antigravity-env/lib/python3.14/site-packages")

import haptic_engine
from haptic_engine import (
    MIN_FRAME_INTERVAL,
    BLE_COMMAND_THROTTLE,
    FRAME_UNDERRUN_TIMEOUT,
    HAPTIC_PATTERNS,
    generate_triangle_wave,
    triangle_wave,
    generate_ramp,
    ramp,
    FrameHapticBuffer,
    HapticControllerBase
)


class MockHapticController(HapticControllerBase):
    """Mock-Controller zur Überprüfung der gesendeten Befehle und Not-Aus Aktionen."""

    def __init__(self):
        self.commands = []
        self.stopped = False
        self.stop_contexts = []
        self.vibrations = []
        self.airs = []

    async def vibrate(self, strength: int, time_sec: float = 0, context: str = ""):
        self.vibrations.append((strength, time_sec, context))
        self.commands.append(f"Vibrate:{strength}")
        return {"success": True, "level": strength}

    async def air(self, level: int, time_sec: float = 0, context: str = ""):
        self.airs.append((level, time_sec, context))
        self.commands.append(f"Air:{level}")
        return {"success": True, "level": level}

    async def stop(self, context: str = "Stop"):
        self.stopped = True
        self.stop_contexts.append(context)
        self.commands.append(f"Stop ({context})")
        return {"success": True, "command": "stop", "context": context}

    def heartbeat(self):
        pass

    def get_status(self):
        return {"connected": True}

    async def play_pattern(self, name: str, duration_sec=None, context="Pattern"):
        return {"success": True}

    async def play_triangle(self, min_level: int, max_level: int, step_duration=0.2, duration_sec=None, context="Triangle"):
        return True

    async def play_ramp(self, start_level: int, end_level: int, duration_sec=1.0, step_duration=0.2, context="Ramp"):
        return True


class TestHapticEnginePatterns(unittest.TestCase):
    """Tests für native Wellenformen und Pattern-Profile."""

    def test_timing_constants(self):
        self.assertEqual(MIN_FRAME_INTERVAL, 0.20)
        self.assertEqual(BLE_COMMAND_THROTTLE, 0.08)
        self.assertEqual(FRAME_UNDERRUN_TIMEOUT, 1.0)

    def test_haptic_patterns_dictionary(self):
        self.assertIn("low", HAPTIC_PATTERNS)
        self.assertIn("med", HAPTIC_PATTERNS)
        self.assertIn("high", HAPTIC_PATTERNS)
        self.assertIn("crescendo", HAPTIC_PATTERNS)
        self.assertIn("decrescendo", HAPTIC_PATTERNS)

        # low: 2-6 (10-30%), 800ms Zyklus
        low = HAPTIC_PATTERNS["low"]
        self.assertEqual(low["min_level"], 2)
        self.assertEqual(low["max_level"], 6)
        self.assertEqual(low["cycle_duration"], 0.80)
        self.assertEqual(low["levels"], [2, 3, 4, 5, 6, 5, 4, 3])

        # med: 8-12 (40-60%), 400ms Zyklus
        med = HAPTIC_PATTERNS["med"]
        self.assertEqual(med["min_level"], 8)
        self.assertEqual(med["max_level"], 12)
        self.assertEqual(med["cycle_duration"], 0.40)
        self.assertEqual(med["levels"], [8, 10, 12, 10])

        # high: 14-18 (70-90%), 200ms Takt
        high = HAPTIC_PATTERNS["high"]
        self.assertEqual(high["min_level"], 14)
        self.assertEqual(high["max_level"], 18)
        self.assertEqual(high["cycle_duration"], 0.20)
        self.assertEqual(high["levels"], [14, 18])

    def test_generate_triangle_wave(self):
        # 1 voller Zyklus für 2..4
        gen = generate_triangle_wave(min_level=2, max_level=4, step_duration=0.1, duration_sec=0.4)
        points = list(gen)
        levels = [p[0] for p in points]
        # Zyklus: 2, 3, 4, 3
        self.assertEqual(levels, [2, 3, 4, 3])
        for _, dt in points:
            self.assertGreaterEqual(dt, BLE_COMMAND_THROTTLE)

    def test_generate_ramp(self):
        # Crescendo 0 auf 10 in 1s mit 0.2s Steps (5 Intervalle, 6 Punkte)
        gen = generate_ramp(start_level=0, end_level=10, duration_sec=1.0, step_duration=0.2)
        points = list(gen)
        levels = [p[0] for p in points]
        self.assertEqual(levels[0], 0)
        self.assertEqual(levels[-1], 10)
        self.assertTrue(all(x <= y for x, y in zip(levels, levels[1:])), "Ramp must be monotonically increasing")

    def test_async_triangle_wave(self):
        async def run():
            vals = []
            async for lvl in triangle_wave(2, 4, step_duration=0.08, duration_sec=0.24):
                vals.append(lvl)
            return vals

        res = asyncio.run(run())
        self.assertGreaterEqual(len(res), 2)
        self.assertTrue(all(2 <= v <= 4 for v in res))

    def test_frame_buffer_push_and_disconnect(self):
        async def run():
            mock_ctrl = MockHapticController()
            buf = FrameHapticBuffer(controller=mock_ctrl, sample_interval=0.05)
            await buf.push_frame(strength=5, context="test_f1")
            await buf.push_frame(strength=10, context="test_f2")
            await asyncio.sleep(0.08)
            # Simuliere Client Disconnect
            await buf.on_client_disconnected("Client closed tab")
            return mock_ctrl

        ctrl = asyncio.run(run())
        self.assertTrue(ctrl.stopped)
        self.assertTrue(any("FAILSAFE_CLIENT_DISCONNECT" in ctx for ctx in ctrl.stop_contexts))

    def test_frame_buffer_underrun_failsafe(self):
        async def run():
            mock_ctrl = MockHapticController()
            buf = FrameHapticBuffer(controller=mock_ctrl, sample_interval=0.05)
            # Push 1 frame
            await buf.push_frame(strength=8, context="single_frame")
            # Nach 0.05s ist der Frame verarbeitet. Danach tritt Pufferunterlauf ein.
            # Timeout nach 1.0s. Wir warten 1.15s
            await asyncio.sleep(1.15)
            return mock_ctrl

        ctrl = asyncio.run(run())
        self.assertTrue(ctrl.stopped)
        self.assertTrue(any("FAILSAFE_BUFFER_UNDERRUN" in ctx for ctx in ctrl.stop_contexts))


if __name__ == "__main__":
    unittest.main()
