# Checkpoint 1G Visual Battle State Enrichment

## 資料流

Checkpoint 1G 是 overlay，不是 1F replacement：

```text
1A PTS／approved ROI + 1B visual candidates + raw video
                         ↓ single sequential decode
Team / Selected / Menu / HP-status visual observations
                         ↓ temporal tracking + entity evidence edges
1E reviewed timeline + 1F sparse snapshots
                         ↓ deterministic fusion
local versioned Pokémon KB exact alias resolution
                         ↓
13 formal JSON outputs + non-blocking engineering Review Pack
```

`checkpoint1g_frame_extractor.py` 是唯一讀影片的模組。它關閉 OpenCV orientation auto，依 1A metadata rotation，並逐 ordinal 對照 ffprobe PTS index；frame count、尺寸、rotation、`CAP_PROP_POS_FRAMES` 與 request completeness 任一失敗都不提交 output。

## Parser 與 tracker 責任

- `team_selection_parser.py`：每側六格 partial roster、Selected Four UI order、icon fingerprint candidate edge。可讀文字只做 Unicode NFKC 後的 exact alias resolution，保存 Top-K canonical species candidates；低於 acceptance threshold 只輸出 `unresolved_candidate`，不合併 entity。
- `move_menu_parser.py`：31 個 candidate 各一 observation。raw OCR、保守 fuzzy correction、selecting slot evidence 與 rejection reason 都保留；`chosen_move`／`target` 預設 unknown。
- `hp_status_tracker.py` 與 `visual_state_tracking.py`：精確數字、OCR %、visual bar estimate 分型。visual bar 必須同時看到固定面板長白色 outline 與健康色 horizontal run；0.5 秒相鄰值做 median，面板消失不清 slot。
- `entity_resolution.py`：優先使用 KB canonical species ID，但不以名稱作唯一 key；同 side duplicate species 不合併。所有 accepted 或 unresolved candidate edges 都保存 rule、confidence 與 provenance。
- `decision_cycles.py`：以 Move Menu cluster 建 half-open cycles；`is_official_turn_number=false`。
- `enriched_state_fusion.py`：71 個 1F snapshots 一對一產生 enriched snapshot，visual data 只覆蓋對應 overlay 欄位，完整 base snapshot 仍內嵌可追溯。

## Knowledge 與 confidence policy

欄位明確區分 `known`、`observed`、`inferred`、`unknown`、`conflicted`、`not_applicable`。Visual HP 只標為 estimate；低信心資料保留為 `observation_only`。Apple Vision 必須固定本機 `zh-Hant`；probe 使用 production 相同 `recognize()` path，任一 runtime error 都中止 transaction，且不可觸發雲端 fallback。沒有 direct timeline evidence 的 HP change 只寫 `cause=unknown`。

Pokémon Knowledge Base 固定載入 `knowledge/pokemon/v1/`，並以 manifest 中的 SHA-256 驗證版本內容。它只提供 canonical species IDs、forms、aliases、繁體中文名稱、regulation availability 與分 domain sprite metadata；不包含 embeddings、vector database 或模型。Regulation availability 與未下載的 sprite metadata 只作 reference/provenance，不會反向提高 OCR 或 visual identity confidence。

## 正式輸出與 Review Pack

`outputs/checkpoint-1g/` 的 13 個 JSON 各自有 schema；manifest 記錄所有 frozen input 與 payload hashes。`outputs/checkpoint-1g-review/` 提供 roster、Selected Four、Move Menu 分頁、HP tracks、HP change、active slot、entity resolution、decision cycle、71 個 enriched snapshot 摘要，以及 uncertainty／coverage indexes。Review Pack 不阻塞 Goal，也不將低信心資料永久刪除。

兩個 output 使用同一 paired transaction：非點號 staging 完整產生、schema/hash/path/frozen gate 通過後才一起 replace；失敗回復上一版。commit 前後會移除 `.DS_Store`、清除 BSD hidden flag，且拒絕非空 ` 2` conflict directory。

## 刻意不做

本階段不執行 simulator、damage calculator、招式合法性校正、官方 turn reconstruction、Replay Analysis、Rule Checker、Checkpoint 1H 或 GUI。對手未出場 species、未顯示 moveset、target 與 HP exact values 都不得由遊戲知識補造。
