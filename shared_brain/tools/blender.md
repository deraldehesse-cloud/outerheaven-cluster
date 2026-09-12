# Blender Pipeline & 3D Export Knowledge Base

## Blender CLI & Headless Pipeline
- **Command-line Execution**:
  blender -b -P script.py -- [args]
- **Zielformate**:
  - GLTF 2.0 Binary (.glb) für WebGL / Three.js Viewer & Godot 4
  - Wavefront (.obj + .mtl) für Fallback & Replay-Engine

## Export-Settings (GLTF 2.0 / GLB)
- **Coordinate System**:
  - Blender: Z-Up -> GLTF Export: Y-Up (Auto-Transform aktivieren).
- **Geometrie**:
  - Apply Modifiers = True (Bevel, Subdivision, Edge Split).
  - Include Normals = True.
  - Tangents = True (für präzise Normal-Map-Darstellung im WebGL-Viewer).
- **Materialien & Texturen**:
  - PBR Metallic-Roughness Workflow.
  - Texturen im GLB gebündelt (Embedded).
  - Maximale Texturauflösung: 2048x2048 (4096x4096 nur für Hero-Assets).
  - Vertex Colors: Export Vertex Colors = True (für XPU Multi-Angle Fusion).

## Optimierung für Real-Time Rendering
- Dreiecks-Budget (Triangle Count):
  - Game Props / Bowling Pins: < 1,500 Triangles
  - Charakter / Avatar-Meshes: 15,000 - 35,000 Triangles
  - Level / Scene Meshes: < 100,000 Triangles
