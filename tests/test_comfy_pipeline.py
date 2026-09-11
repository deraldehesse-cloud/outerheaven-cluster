#!/usr/bin/env python3
"""
Unit tests for ComfyUI Pipeline Builder:
- Schema parsing & type normalization
- Intent archetype validation
- Type mismatch & required slot detection
- Native ComfyUI prompt JSON generation
"""

import sys
import unittest
import json
from pathlib import Path

# Add mission-control to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "mission-control"))

import comfy_schema
from comfy_schema import (
    ComfySchemaManager,
    PipelineWorkflow,
    PipelineStep,
    PipelineConnection,
    are_types_compatible,
    validate_pipeline,
    convert_pipeline_to_comfy_prompt,
    FALLBACK_OBJECT_INFO,
    parse_comfy_object_info
)

class TestComfyPipelineBuilder(unittest.TestCase):

    def setUp(self):
        self.cache_file = BASE_DIR / "mission-control" / "data" / "object_info_cache.json"
        self.archetypes_dirs = [
            BASE_DIR / "workflows" / "archetypes",
            BASE_DIR / "mission-control" / "workflows" / "archetypes"
        ]
        self.manager = ComfySchemaManager(
            cache_file=self.cache_file,
            archetypes_dirs=self.archetypes_dirs
        )

    def test_01_schema_parsing(self):
        """Verify parsing of ComfyUI object_info format into typed NodeSchema."""
        nodes = self.manager.nodes
        self.assertIn("KSampler", nodes)
        self.assertIn("CheckpointLoaderSimple", nodes)
        self.assertIn("VAEDecode", nodes)
        self.assertIn("SaveImage", nodes)

        ksampler = nodes["KSampler"]
        self.assertEqual(ksampler.category, "sampling")
        self.assertIn("model", ksampler.input_types.required)
        self.assertEqual(ksampler.input_types.required["model"], "MODEL")
        self.assertIn("positive", ksampler.input_types.required)
        self.assertEqual(ksampler.input_types.required["positive"], "CONDITIONING")
        self.assertIn("LATENT", ksampler.output_types)

        # Check payload format for GET /api/comfy/nodes
        payload = self.manager.get_nodes_payload()
        self.assertIn("nodes", payload)
        self.assertIn("count", payload)
        sample = payload["nodes"]["KSampler"]
        self.assertIn("name", sample)
        self.assertIn("category", sample)
        self.assertIn("input_types", sample)
        self.assertIn("required", sample["input_types"])
        self.assertIn("output_types", sample)

    def test_02_archetypes_loading(self):
        """Verify that all 3 archetypes exist and are loaded properly."""
        archetypes = self.manager.get_archetypes()
        arch_ids = {a["id"] for a in archetypes}
        self.assertIn("text_to_image", arch_ids)
        self.assertIn("image_to_image_controlnet", arch_ids)
        self.assertIn("video_generation", arch_ids)

    def test_03_text_to_image_validation(self):
        """Validate the Text-to-Image archetype workflow."""
        data = self.manager.get_archetype_by_id("text_to_image")
        self.assertIsNotNone(data)
        wf = PipelineWorkflow(**data)
        res = validate_pipeline(wf, self.manager.nodes)
        self.assertTrue(res.valid, f"T2I Validation failed: {res.errors}")
        self.assertEqual(len(res.errors), 0)

        # Convert to ComfyUI prompt JSON
        prompt_json = convert_pipeline_to_comfy_prompt(wf, self.manager.nodes)
        self.assertIn("prompt", prompt_json)
        prompt = prompt_json["prompt"]
        self.assertEqual(len(prompt), len(wf.steps))

        # Verify CheckpointLoaderSimple is step 1
        self.assertEqual(prompt["1"]["class_type"], "CheckpointLoaderSimple")
        # Verify KSampler is wired to Checkpoint model ["1", 0]
        step_id_map = {s.id: str(i) for i, s in enumerate(wf.steps, start=1)}
        ksampler_num = step_id_map["step_5"]
        ckpt_num = step_id_map["step_1"]
        self.assertEqual(prompt[ksampler_num]["inputs"]["model"], [ckpt_num, 0])

    def test_04_image_to_image_controlnet_validation(self):
        """Validate the Image-to-Image / ControlNet archetype workflow."""
        data = self.manager.get_archetype_by_id("image_to_image_controlnet")
        self.assertIsNotNone(data)
        wf = PipelineWorkflow(**data)
        res = validate_pipeline(wf, self.manager.nodes)
        self.assertTrue(res.valid, f"I2I ControlNet validation failed: {res.errors}")

        prompt_json = convert_pipeline_to_comfy_prompt(wf, self.manager.nodes)
        self.assertIn("prompt", prompt_json)
        self.assertEqual(len(prompt_json["prompt"]), len(wf.steps))

    def test_05_video_generation_validation(self):
        """Validate the Video Generation (AnimateDiff) archetype workflow."""
        data = self.manager.get_archetype_by_id("video_generation")
        self.assertIsNotNone(data)
        wf = PipelineWorkflow(**data)
        res = validate_pipeline(wf, self.manager.nodes)
        self.assertTrue(res.valid, f"Video generation validation failed: {res.errors}")

        prompt_json = convert_pipeline_to_comfy_prompt(wf, self.manager.nodes)
        self.assertIn("prompt", prompt_json)
        self.assertEqual(len(prompt_json["prompt"]), len(wf.steps))

    def test_06_type_compatibility_and_mismatch_detection(self):
        """Verify that incompatible connections (e.g. IMAGE -> MODEL) are caught."""
        self.assertTrue(are_types_compatible("LATENT", "LATENT"))
        self.assertTrue(are_types_compatible("MODEL", "MODEL"))
        self.assertTrue(are_types_compatible("IMAGE", "IMAGE"))
        self.assertFalse(are_types_compatible("IMAGE", "MODEL"))
        self.assertFalse(are_types_compatible("LATENT", "IMAGE"))
        self.assertFalse(are_types_compatible("CONDITIONING", "MODEL"))

        # Create invalid workflow
        invalid_wf = PipelineWorkflow(
            id="test_invalid",
            name="Invalid Pipeline",
            steps=[
                PipelineStep(id="step_1", node_type="LoadImage"),
                PipelineStep(id="step_2", node_type="KSampler")
            ],
            connections=[
                PipelineConnection(
                    from_node="step_1", output_name="IMAGE", output_type="IMAGE",
                    to_node="step_2", input_name="model", input_type="MODEL"
                )
            ]
        )
        val = validate_pipeline(invalid_wf, self.manager.nodes)
        self.assertFalse(val.valid)
        self.assertTrue(any("Typ-Mismatch" in e for e in val.errors))

    def test_07_missing_required_slot_detection(self):
        """Verify that unfulfilled required slots are flagged as errors."""
        incomplete_wf = PipelineWorkflow(
            id="test_incomplete",
            name="Incomplete Pipeline",
            steps=[
                PipelineStep(id="step_1", node_type="KSampler")
            ],
            connections=[]
        )
        val = validate_pipeline(incomplete_wf, self.manager.nodes)
        self.assertFalse(val.valid)
        self.assertTrue(any("model" in e for e in val.errors))
        self.assertTrue(any("positive" in e for e in val.errors))

if __name__ == "__main__":
    unittest.main(verbosity=2)

from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
import server

class TestComfyServerEndpoints(AioHTTPTestCase):

    async def get_application(self):
        return server.create_app()

    @unittest_run_loop
    async def test_http_nodes_endpoint(self):
        """Test GET /api/comfy/nodes returns 200 and schema payload."""
        headers = {"X-Cluster-Key": server.CLUSTER_API_KEY}
        resp = await self.client.request("GET", "/api/comfy/nodes", headers=headers)
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIn("nodes", data)
        self.assertIn("KSampler", data["nodes"])

    @unittest_run_loop
    async def test_http_archetypes_endpoint(self):
        """Test GET /api/comfy/archetypes and GET /api/comfy/archetypes/{id}."""
        headers = {"X-Cluster-Key": server.CLUSTER_API_KEY}
        resp = await self.client.request("GET", "/api/comfy/archetypes", headers=headers)
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIn("archetypes", data)
        self.assertGreaterEqual(len(data["archetypes"]), 3)

        # Detail endpoint
        resp2 = await self.client.request("GET", "/api/comfy/archetypes/text_to_image", headers=headers)
        self.assertEqual(resp2.status, 200)
        arch = await resp2.json()
        self.assertEqual(arch["id"], "text_to_image")

    @unittest_run_loop
    async def test_http_pipeline_validate_and_export(self):
        """Test POST /api/comfy/pipeline/validate and export."""
        headers = {"X-Cluster-Key": server.CLUSTER_API_KEY}
        resp_arch = await self.client.request("GET", "/api/comfy/archetypes/text_to_image", headers=headers)
        arch = await resp_arch.json()

        # Validate
        resp_val = await self.client.request("POST", "/api/comfy/pipeline/validate", json=arch, headers=headers)
        self.assertEqual(resp_val.status, 200)
        val = await resp_val.json()
        self.assertTrue(val["valid"])

        # Export
        resp_exp = await self.client.request("POST", "/api/comfy/pipeline/export", json=arch, headers=headers)
        self.assertEqual(resp_exp.status, 200)
        exp = await resp_exp.json()
        self.assertIn("prompt", exp)
        self.assertEqual(len(exp["prompt"]), 7)

