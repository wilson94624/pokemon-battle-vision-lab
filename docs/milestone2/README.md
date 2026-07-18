# Battle Vision Milestone 2

Milestone 2 transforms the completed Battle Vision Engine into a practical, local-first,
single-user desktop product for replay review and coaching.

The Milestone 1 Engine is an immutable backend boundary. Milestone 2 consumes it through its
existing CLI, schemas, manifests, hashes, and replay artifacts. Product code must not import
checkpoint internals to bypass those contracts, rewrite Battle Facts, merge Rule
Interpretations into observations, or allow AI to inspect replay video directly.

The approved initial architecture baseline is:

- [Milestone 2 Product Architecture](product_architecture.md)

The recommended implementation order is:

```text
Replay Workspace
    → Human Review
    → Evidence Explorer
    → Rule Interpretation
    → Deterministic Coaching Contract
    → AI Coach
    → Personal Learning Loop
```

Future Milestone 2 architecture, milestone, and acceptance documents must:

- treat `product_architecture.md` as their baseline;
- identify any proposed deviation explicitly;
- preserve the normative [Domain Principles](../../architecture/domain_principles.md) and
  [Evidence Model](../../architecture/evidence_model.md);
- keep product state and AI output downstream of immutable Engine truth;
- record whether new decisions are accepted or provisional;
- avoid modifying Milestone 1 artifacts merely to simplify the product layer.

Milestone 2A begins with a separate product package and read-only discovery of existing
replay workspaces. GUI, SQLite persistence, background orchestration, Human Review UI, and AI
coaching are later slices and are not prerequisites for the initial product boundary.
