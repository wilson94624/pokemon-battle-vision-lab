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