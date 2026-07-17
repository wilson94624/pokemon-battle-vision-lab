# AGENTS.md

# Repository Architecture

The following documents define the architecture of this repository.

- architecture/domain_principles.md
- architecture/evidence_model.md

These architecture documents are normative.

When implementing new features, follow these documents before introducing new behavior.

If a conflict exists, these architecture documents take precedence over task-specific implementation decisions.

---

## Architecture Stability

Prefer extending the existing architecture over redesigning it.

Avoid introducing new abstractions, pipelines, checkpoint structures, or large-scale refactors unless they provide clear architectural value.

Incremental evolution is preferred over replacement.

---

## macOS Generated Output Visibility Policy

- Never set the hidden flag on any generated file or directory.
- Avoid dot-prefixed staging directories on macOS.
- Do not use dot-prefixed (.) staging directories on macOS unless there is a documented technical requirement.
- Prefer names such as `checkpoint-1b-review.tmp-<UUID>` instead of `.checkpoint-1b-review.tmp-<UUID>`.
- Transactional output replacement must preserve the previous valid output if generation fails.
- After a successful replace, recursively clear hidden flags from the final output tree.
- Validate that no final output item has the macOS BSD `hidden` flag.
- The output is considered invalid if any hidden item remains.
- Do not rely on Finder, iCloud, or File Provider to repair visibility automatically.
- Successful reruns must keep only the newest generated output and remove obsolete generated artifacts safely.

Suggested validation:

```bash
find <output-directory> -exec ls -ldO {} + | grep ' hidden '
```

The command must return no lines.

Regression requirements:

- Every generated output must remain visible in Finder.
- Every generated output must be accessible from VS Code/Codex Explorer.
- No generated artifact may carry the macOS BSD `hidden` flag.

---

# Version

Version: 2.1

Last Updated: 2026-07

---

## Repository-first Workflow

- Search the repository before creating new modules.
- Reuse existing implementations whenever practical.
- Extend existing pipelines before introducing parallel implementations.
- Do not reread historical checkpoint documents unless required.
- Keep accepted architecture stable unless the task explicitly changes it.

---

## Long-running Goal Rules

- Keep permanent rules in AGENTS.md.
- Keep task-specific milestones and progress under `plans/`.
- Continue through recoverable failures using conservative degradation.
- Do not invent extra checkpoints unless requested.

---

## Token & Context Efficiency

- Read only the files relevant to the current task.
- Prefer targeted search over opening large files.
- Avoid repeating repository summaries.
- Avoid repeating rules already defined here.
- Batch inspections and tests when practical.

---

## Open-source Discovery

Before implementing a major subsystem or external integration:

1. Check official documentation.
2. Check mature open-source projects.
3. Check relevant Awesome lists.
4. Reuse proven designs when appropriate.

Document whether a source is adopted, referenced, or rejected.

Small internal features do not require external research.

---

## Pokémon Vision Rules

- Prefer a local versioned Pokémon Knowledge Base.
- Separate different visual domains (HUD, Team Preview, Sprite, Icon, 3D).
- Recognition should generally produce Top-K candidates with confidence.
- Meta usage is not visual evidence.

---

## OCR Runtime Rules

- Production OCR and probe must execute the same runtime path.
- Runtime failures require a minimal reproducible test.
- Do not silently switch to cloud OCR.

---

## Scope Control

Unless explicitly requested, do not begin:

- Replay Analysis
- GUI
- Deployment
- Model training
- Vector database integration

Do not introduce Pokémon rule-based reasoning unless the current checkpoint explicitly requires it.

---

## Git Safety

- Inspect `git status` before editing.
- Preserve unrelated user changes.
- Do not commit or push unless explicitly requested.

---

## Evidence-first Principle

Every emitted Battle Fact must be traceable to one or more concrete observations.

When evidence is insufficient:

- use `unknown`
- use `ambiguous`
- preserve uncertainty

Never:

- fabricate observations
- infer Battle Facts from Pokémon knowledge
- infer Battle Facts from simulator behavior

Observations create Battle Facts.

Battle Facts may later be interpreted by Knowledge.

Knowledge never modifies existing Battle Facts.

Knowledge interprets Battle Facts.

Knowledge does not create Battle Facts.
