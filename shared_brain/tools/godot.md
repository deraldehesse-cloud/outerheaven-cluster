# Godot 4 & Jolt Physics Knowledge Base

## Core Engine & Architektur
- **Engine Version**: Godot 4.3+ (Forward+ Renderer / Mobile Fallback)
- **Physics Engine**: Godot Jolt Physics (High-performance multi-threaded C++ solver)
- **Projekt-Scope**: projekt-dr-franks-bowling, projekt-catlord

## Jolt Physik-Tricks & Best Practices
- **Collision Shapes**:
  - Konvexe Hüllen (ConvexPolygonShape3D) und Primitives (Sphere, Box, Capsule) bevorzugen.
  - Niemals ConcaveMeshShape3D auf dynamischen RigidBodies verwenden (führt zu Solver-Explosionen).
- **CCD (Continuous Collision Detection)**:
  - Bei High-Speed-Objekten (z. B. Bowlingkugeln, Geschossen) immer continuous_cd = true aktivieren.
- **Physics Tick Rate**:
  - physics_ticks_per_second = 60 (Default). Für Pin- und Pinsetter-Simulationen ggf. 90-120 Ticks bei max_physics_steps_per_frame = 8.
- **Damping & Rebound**:
  - Linear Damping: 0.05 - 0.15 für realistische Bahnreibung.
  - Angular Damping: 0.1 für gleichmäßiges Rollen ohne Rutschen.

## USBC Standards (United States Bowling Congress)
- **Kugeldurchmesser**: 8.500 bis 8.595 Zoll (21.59 cm - 21.83 cm) -> Godot Radius = 0.108m.
- **Kugelgewicht**: Maximal 16 lbs (7.257 kg).
- **Pin-Höhe**: 15.0 Zoll (38.1 cm) -> Godot Height = 0.381m.
- **Pin-Gewicht**: 3 lbs 6 oz bis 3 lbs 10 oz (1.53 kg - 1.64 kg).
- **Bahnlänge**: 60 Fuß (18.288 m) von Foul-Line bis Headpin.

## Reusable Script Snippets
- **Ball Velocity & Hook Application**:
  Vector3 forward_force = -transform.basis.z * throw_speed;
  Vector3 rev_spin = transform.basis.y * hook_rotation_speed;
  apply_central_impulse(forward_force);
  apply_torque_impulse(rev_spin);
