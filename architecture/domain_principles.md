# Battle Vision Domain Principles

## Purpose

This document defines the domain principles for the Pokémon Battle Vision pipeline.

These principles describe what each stage is responsible for, what constitutes evidence, and what the system must never infer.

---

# Core Philosophy

Vision precedes knowledge.

The pipeline must first reconstruct what is directly observable from the battle video.

Only after observable battle facts have been reconstructed may external game knowledge be applied.

Game knowledge must never fabricate observations.

---

# Evidence First

Every Battle Fact must be supported by concrete evidence.

If evidence is insufficient, the system must preserve uncertainty instead of guessing.

Use:

- unknown
- ambiguous
- unresolved

instead of inventing facts.

---

# Responsibility Separation

## Battle Text

Responsible for:

- Executed move
- Ability activation
- Item activation
- Status messages
- Weather messages
- Terrain messages
- KO messages

Battle Text is the primary source of battle events.

---

## Move Menu

Responsible only for:

- Available moves

Move Menu never indicates which move was selected.

A move is considered executed only after Battle Text confirms it.

---

## HP Observation

Responsible for:

- Observed HP values
- HP percentage
- HP changes

Not responsible for:

- Damage amount
- Damage source
- Remaining hidden HP

---

## Timeline

Responsible for:

- Temporal ordering
- Event ordering
- Synchronization between observations

---

## Pokémon Knowledge Base

Responsible for:

- Species identity
- Forms
- Aliases
- Metadata

Not responsible for:

- Creating battle events
- Explaining observations

---

## Rule Knowledge

Responsible for:

- Regulation legality
- Available Pokémon
- Available moves
- Available items
- Available abilities

Rule knowledge must never create observations.

---

# Forbidden

The system must never:

- Guess unseen moves.
- Guess targets.
- Guess abilities.
- Guess held items.
- Guess damage rolls.
- Guess player intentions.
- Infer battle facts from simulator knowledge.

Only observable evidence may create Battle Facts.