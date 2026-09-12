# ComfyUI Knowledge Base & Best Practices

## Architektur & Hardware-Spezifikation
- **Host**: Outer Heaven (HP Z620 Dual Xeon E5-2690 v2, 64 GB RAM)
- **Compute Device**: Sparkle Intel Arc A770 16GB (torch.xpu)
- **Base Endpoint**: http://127.0.0.1:8188
- **Execution Flags**: --listen 0.0.0.0 --port 8188 --gpu-only --use-pytorch-cross-attention --disable-pinned-memory

## Bewährte Checkpoints & Modelle
- **SD1.5 / Cyberpunk / Fantasy**:
  - juggernaut_aftermath.safetensors (Hervorragende Detailtreue, schnelle Generierung auf Arc A770)
  - cyberrealistic_v42.safetensors (Realistische Texturen, präzise Anatomie)
- **Anime / Stylized**:
  - counterfeit_v30.safetensors
- **SDXL / Realistic**:
  - realvisxl_v40.safetensors (Hohe Auflösung 1024x1024, erfordert VRAM-Management)

## Optimale Sampler- & Scheduler-Settings
- **KSampler**:
  - sampler_name: dpmpp_2m oder euler_ancestral
  - scheduler: karras
  - steps: 24 - 30 (26 optimaler Kompromiss aus Latenz und Qualität)
  - cfg: 6.0 - 7.5 (Optimal: 6.5 für natürliche Kontraste ohne Übersättigung)
  - denoise: 1.0 (für Text2Img), 0.45 - 0.65 (für Img2Img / Upscale)

## LoRA-Kombinationen
- **Tactical / Mech Gear**:
  - tactical_gear_v1.safetensors (Weight: 0.65 - 0.8)
  - cyber_lighting_offset.safetensors (Weight: 0.5)
- **Regel**: Niemals mehr als 3 LoRAs parallel mit Summen-Gewichtung > 2.0 kombinieren, um Clipping auf Intel Arc zu verhindern.

## Funktionierende Core- & Custom-Nodes
- CheckpointLoaderSimple
- CLIPTextEncode
- VAEDecode / VAEEncode
- KSampler
- EmptyLatentImage
- SaveImage / websocket_image_save
- comfyui_controlnet_aux (Depth, OpenPose, Canny)

## Fehlende / Manuelle Nodes
*(Hier werden Nodes automatisch erfasst, die im Workflow angefordert, aber noch nicht im Cluster installiert sind)*
