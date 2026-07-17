# Evidence Model

## Purpose

This document defines the confidence hierarchy of the Battle Vision pipeline.

The pipeline separates four layers:

Observation

↓

Battle Fact

↓

Inference

↓

Analysis

Each layer has different responsibilities.

---

# Layer 1 — Observation

Observations are directly extracted from the video.

Examples:

- Battle Text
- HP %
- Status icon
- Weather icon
- Active Pokémon
- Move Menu
- Switch animation

Observations should contain no interpretation.

---

# Layer 2 — Battle Fact

Battle Facts are reconstructed from one or more observations.

Examples:

- Swampert used Earthquake.
- Rotom switched in.
- Amoonguss fainted.
- Rain ended.
- Tailwind was activated.

Every Battle Fact must reference supporting observations.

---

# Layer 3 — Inference

Inference combines Battle Facts with game knowledge.

Examples:

- Rotom probably has Levitate.
- Opponent is likely Choice Scarf.
- The hidden Pokémon is likely Incineroar.

Inference must never modify Battle Facts.

Inference is optional.

---

# Layer 4 — Analysis

Analysis evaluates decisions.

Examples:

- This switch was risky.
- Protect would have been safer.
- Tailwind should have been used earlier.

Analysis is always downstream of Battle Facts.

Analysis must never rewrite observations.

---

# Design Rule

Information may flow downward only.

Observation

↓

Battle Fact

↓

Inference

↓

Analysis

Higher layers must never modify lower layers.

Battle Facts are immutable once reconstructed.