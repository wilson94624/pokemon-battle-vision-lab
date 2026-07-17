# Checkpoint 1G–1H Milestone Hygiene Report

## 結果

- Milestone commit：`df1367a Checkpoint 1G-1H: add visual enrichment and battle fact reconstruction`
- Commit 前 tracked worktree：67 個預期的 1G／1H files，沒有 `outputs/` 被 stage。
- Commit 後 tracked worktree：clean。

## 安全清理

已刪除下列非正式 artifacts：

- 7 個完全空白的 File Provider 衝突目錄：`checkpoint-1g 3/4/5`、`checkpoint-1g-review 3/4/5`、`checkpoint-1e-review 3`
- repository 內 Finder `.DS_Store`
- `src/pokemon_battle_vision/__pycache__` 與其中 bytecode cache
- 所有 generated output 的 BSD `hidden` flag；tracked `outputs/.gitkeep` 只清除 flag，內容未變

沒有刪除任何非空或用途不明目錄，也沒有修改任何有效 1A–1H payload。Hygiene 後沒有 unresolved suspicious path。

## 驗證

- `pytest -m 'not slow'`：275 passed、7 deselected
- sandbox 外真實 Apple Vision 1G slow integration + 1H deterministic slow integration：2 passed
- `git diff --cached --check`：通過
- `outputs/`：無 `.DS_Store`、cache、tmp、backup、名稱帶 ` 2/3/4/5` 的衝突目錄或 BSD hidden flag

1G slow test 在 Codex sandbox 內會由 Apple Vision 回傳 `Vision request failed without NSError`；以相同 test 在 sandbox 外執行後通過，符合既有 runtime MRE 與 production-path policy。
