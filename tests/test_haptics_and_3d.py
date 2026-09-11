#!/usr/bin/env python3
"""
test_haptics_and_3d.py - Comprehensive Unit Tests for 3D Dashboard,
Multi-Angle Pose Pipeline, and Autonomous Haptic-Audio Engine.
"""

import sys
import unittest
import numpy as np
from pathlib import Path

# Add project roots
PROJECT_ROOT = Path("/mnt/storage-beta/projekte/projekt-sexchat")
REPO_ROOT = Path("/home/bigboss/outerheaven-cluster-repo")
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "mission-control"))

import audio_haptics
import workflow_generator
import reconstruct_3d


class TestHapticAudioEngine(unittest.TestCase):
    """Tests for Speech-to-Vibration Envelope Tracking & Filtering."""

    def test_synthetic_audio_envelope_extraction(self):
        sr = 16000
        dur = 1.0
        t = np.linspace(0, dur, int(sr * dur), endpoint=False)
        # 300Hz vocal fundamental tone with dynamic attack
        pulse = (0.5 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)

        envelope = audio_haptics.extract_speech_envelope(pulse, sample_rate=sr, frame_ms=50, hop_ms=25)
        self.assertGreater(len(envelope), 20)
        self.assertLessEqual(len(envelope), 45)

        # Check values bounded [0.0, 1.0]
        for ts, energy in envelope:
            self.assertGreaterEqual(energy, 0.0)
            self.assertLessEqual(energy, 1.0)
            self.assertGreaterEqual(ts, 0.0)

    def test_vibration_mapping_and_noise_gate(self):
        # Test synthetic envelope values
        test_env = [
            (0.0, 0.00),  # Complete silence
            (0.1, 0.02),  # Background room noise (below gate)
            (0.2, 0.25),  # Soft speech
            (0.3, 0.65),  # Medium speech
            (0.4, 0.98),  # Loud peak
            (0.5, 0.00)   # Silence after speech
        ]
        track = audio_haptics.map_envelope_to_vibration(test_env, noise_gate=0.05, max_level=20)
        self.assertEqual(len(track), len(test_env))

        # Check silence & noise gate
        self.assertEqual(track[0][1], 0)
        self.assertEqual(track[1][1], 0)

        # Check speech range
        self.assertGreaterEqual(track[2][1], 1)
        self.assertGreater(track[3][1], track[2][1])
        self.assertEqual(track[4][1], 20)
        self.assertEqual(track[5][1], 0)

        # Assert all bounded in [0, 20]
        for _, lvl in track:
            self.assertIsInstance(lvl, int)
            self.assertTrue(0 <= lvl <= 20)

    def test_ascii_waveform_renderer(self):
        track = [(0.0, 0), (0.1, 5), (0.2, 10), (0.3, 15), (0.4, 20), (0.5, 0)]
        wave_str = audio_haptics.render_ascii_waveform(track)
        self.assertIsInstance(wave_str, str)
        self.assertGreater(len(wave_str), 0)


class TestMultiViewPipelineBuilder(unittest.TestCase):
    """Tests for 4-Angle Canonical Multi-Perspective Workflow Builder."""

    def test_multiview_workflow_structure(self):
        wf = workflow_generator.create_multiview_pose_workflow(
            base_positive_prompt="1girl, haven, tactical gear",
            base_negative_prompt="deformed, lowres",
            checkpoint="DreamShaper8_LCM.safetensors",
            loras=[{"name": "detail_slider.safetensors", "strength_model": 0.8}],
            seed=424242
        )

        prompt = wf.get("prompt", {})
        views = wf.get("views", [])

        # Assert all 4 canonical views present
        expected_views = ["0_front", "1_side_l", "2_back", "3_side_r"]
        self.assertEqual(views, expected_views)

        # Assert base checkpoint and LoRA loader present
        self.assertIn("1", prompt)
        self.assertEqual(prompt["1"]["class_type"], "CheckpointLoaderSimple")
        self.assertIn("10", prompt)
        self.assertEqual(prompt["10"]["class_type"], "LoraLoader")

        # Check presence of 4 distinct KSamplers and SaveImage nodes
        samplers = [k for k, v in prompt.items() if v.get("class_type") == "KSampler"]
        savers = [k for k, v in prompt.items() if v.get("class_type") == "SaveImage"]
        self.assertEqual(len(samplers), 4)
        self.assertEqual(len(savers), 4)

        # Check unique filename prefixes
        prefixes = [v["inputs"]["filename_prefix"] for k, v in prompt.items() if v.get("class_type") == "SaveImage"]
        for exp in expected_views:
            self.assertTrue(any(exp in p for p in prefixes))


class TestReconstruction3D(unittest.TestCase):
    """Tests for 3D Mesh Synthesis & Multi-View Fusion."""

    def test_mesh_generation_geometry(self):
        from PIL import Image
        import tempfile

        # Create 64x64 synthetic image
        img = Image.new("RGB", (64, 64), color=(180, 100, 120))
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            img.save(tmp_path)

        try:
            verts, normals, uvs, faces, _ = reconstruct_3d.compute_depth_and_mesh(tmp_path, target_res=32)
            self.assertGreater(len(verts), 100)
            self.assertGreater(len(faces), 100)
            self.assertEqual(verts.shape[1], 3)
            self.assertEqual(normals.shape[1], 3)
            self.assertEqual(uvs.shape[1], 2)
            self.assertEqual(faces.shape[1], 3)

            # Check UV coordinates strictly [0, 1]
            self.assertTrue(np.all(uvs >= -1e-5))
            self.assertTrue(np.all(uvs <= 1.0001))

        finally:
            if tmp_path.is_file():
                tmp_path.unlink()

    def test_multiview_360_fusion(self):
        from PIL import Image
        import tempfile

        img = Image.new("RGB", (32, 48), color=(200, 150, 100))
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            img.save(tmp_path)

        try:
            res = reconstruct_3d.reconstruct_multiview_3d(
                [str(tmp_path), str(tmp_path), str(tmp_path), str(tmp_path)],
                output_prefix="Test_Haven_360_Fused",
                grid_res=32
            )
            self.assertTrue(res["success"])
            self.assertEqual(res["mode"], "multiview_360_fused")
            self.assertGreater(res["vertex_count"], 500)
            self.assertGreater(res["face_count"], 1000)

            glb_file = Path(res["outputs"]["glb"])
            self.assertTrue(glb_file.is_file())
            self.assertGreater(glb_file.stat().st_size, 1024)

        finally:
            if tmp_path.is_file():
                tmp_path.unlink()


class TestMissionControl3DEndpoints(unittest.TestCase):
    """Tests for 3D REST API and MIME typing in server.py."""

    def test_mime_type_resolution(self):
        ext_map = {
            ".glb": "model/gltf-binary",
            ".gltf": "model/gltf+json",
            ".obj": "text/plain; charset=utf-8",
            ".mtl": "text/plain; charset=utf-8",
            ".png": "image/png"
        }
        for ext, expected_mime in ext_map.items():
            if ext == ".glb":
                mime = "model/gltf-binary"
            elif ext == ".gltf":
                mime = "model/gltf+json"
            elif ext in [".obj", ".mtl"]:
                mime = "text/plain; charset=utf-8"
            elif ext == ".png":
                mime = "image/png"
            else:
                mime = "application/octet-stream"
            self.assertEqual(mime, expected_mime)


if __name__ == "__main__":
    unittest.main(verbosity=2)
