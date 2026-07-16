# Checkpoint 1E Battle Timeline Architecture Audit

本文件記錄 Checkpoint 1E 修改前的唯讀稽核。資料來源是 Parser 0.2.0 正式重建後的 `outputs/checkpoint-1d/battle_events.json`；共 102 筆事件，`UNKNOWN_EVENT=0`，輸入 SHA-256 為 `66ff5a197ccf89a762f93245d901af939c61857ad9c7d1f0a9c43ad39e5261e7`。

## 1. 時間間隔分布

以相鄰事件的「前一事件 end_time 到下一事件 start_time」計算 101 個間隔：

| 指標 | 結果 |
| --- | ---: |
| 最小值 | -1.4 秒（ABILITY 與 STAT_CHANGE 重疊） |
| 中位數 | 0.9 秒 |
| p90 | 7.5 秒 |
| 最大值 | 44.386667 秒 |
| 重疊 | 1 |
| 0–0.5 秒 | 33 |
| 0.5–1 秒 | 18 |
| 1–2 秒 | 7 |
| 2–3 秒 | 13 |
| 3–5 秒 | 15 |
| 超過 5 秒 | 14 |

結論：相鄰事件多半接近，但長尾很大；時間只能作為候選窗口，不足以證明因果。Mega Evolution 的 activation／completion 甚至相隔 7.5–8.5 秒，必須依 actor 與 action phase 判定，不能只設單一短 gap。

## 2. Major Action 與 consequence

### 通常可開啟 group 的事件

- `MOVE`：29/29 有 actor，是最明確的主要行動，但全部缺少顯式 target。
- `SWITCH`：6 筆均保留 switch targets；雙打開場的兩次 SWITCH 不應互相合併。
- `TRANSFORMATION`：activation／completion 可用相同 actor 與 phase 形成兩階段 chain。
- `BATTLE_RESULT`：應作最後的獨立 group；forfeit 與 win 可在證據足夠時形成結尾 chain。

### 通常是 consequence 的事件

- `MOVE_RESULT`、`DAMAGE_RESULT`、`STATUS`、`STAT_CHANGE`、`VOLATILE_STATUS`、`FAINT`。
- `ABILITY`、`ITEM` 可由 SWITCH／MOVE 觸發，也可能獨立發生，不可固定視為 primary 或 child。

### 可獨立存在的事件

- 沒有可靠來源的 residual `DAMAGE_RESULT`。
- `WEATHER`、`SIDE_CONDITION`、`FIELD_EFFECT` 的開始／結束；若 effect 與前一 MOVE 明確匹配才附著。
- `VOLATILE_STATUS` counter update／effect end；可形成同 effect、同 counter 的多目標 batch，但不得硬掛到更早 MOVE。
- 缺少顯式 MOVE 的 prevented `MOVE_RESULT`，例如定身法阻止招式，應保留為 unlinked，而不是掛到時間最近但語意衝突的羽棲。

## 3. Metadata 完整度

| Event Type | 數量 | 有 actor | 有 target／targets | 有 side |
| --- | ---: | ---: | ---: | ---: |
| MOVE | 29 | 29 | 0 | 13 |
| MOVE_RESULT | 8 | 1 | 7 | 6 |
| DAMAGE_RESULT | 13 | 0 | 13 | 12 |
| STATUS | 2 | 0 | 2 | 2 |
| STAT_CHANGE | 6 | 0 | 6 | 5 |
| VOLATILE_STATUS | 21 | 0 | 21 | 12 |
| FAINT | 4 | 0 | 4 | 2 |
| SWITCH | 6 | 2 | 6 | 0 |
| TRANSFORMATION | 4 | 4 | 0 | 2 |
| ABILITY／ITEM | 2 | 2 | 0 | 0 |
| WEATHER／FIELD_EFFECT／BATTLE_RESULT | 5 | 0 | 0 | 0 |
| SIDE_CONDITION | 2 | 0 | 0 | 2 |

actor／target 缺失不等同 parser 錯誤，而是 OCR 文字本身沒有提供。Timeline 不得用招式知識補出 target。

## 4. MOVE 後的實際下一事件

29 個 MOVE 的直接後繼分布：

| 下一 Event Type | 數量 |
| --- | ---: |
| MOVE_RESULT | 8 |
| VOLATILE_STATUS | 7 |
| STAT_CHANGE | 4 |
| DAMAGE_RESULT | 3 |
| STATUS | 2 |
| WEATHER | 1 |
| SIDE_CONDITION | 1 |
| FIELD_EFFECT | 1 |
| ITEM | 1 |
| MOVE | 1 |

其中部分只是時間相鄰。例如羽棲後出現「烈咬陸鯊因定身法無法使出地震」，其 metadata.move 與前一 MOVE 衝突，必須拒絕因果關聯。

## 5. 常見 chain 的可用證據

- `MOVE → MOVE_RESULT`：若 actor、move、target 任一可比對，才可能高信心；只有時間時僅能建立待審查的 temporal adjacency。
- `MOVE → DAMAGE_RESULT`：recoil 且 MOVE.actor 等於 damage.target 是強證據；一般傷害因 MOVE 缺 target，不能自動宣稱來源。
- `DAMAGE_RESULT → FAINT`：target 完全一致、時間連續且無新 major action 時可高信心關聯。
- `MOVE → STATUS／STAT_CHANGE／VOLATILE_STATUS`：effect 等於 move、或 MOVE.actor 等於 consequence.target 時是結構證據；否則降為待審查。
- `SWITCH → ABILITY`：switch targets 包含 ability.actor 是強證據。
- `TRANSFORMATION → TRANSFORMATION`：相同 actor、activate→change、無中間 major action是強證據，即使 gap 較長。
- residual multi-target：同 type、cause／effect／action／counter 相同且時間連續，可用 `SAME_ACTION` 表示 sibling batch。

## 6. Multi-target 表示

同一 MOVE 可擁有多條 sibling relation edge：

```text
MOVE
├── DAMAGE_RESULT target A
├── DAMAGE_RESULT target B
├── FAINT target A
└── STATUS target B
```

Group 內仍按 source timestamp／source order 排列；不得把 target B 的結果誤當成 target A 結果的 child。若 MOVE 缺 target，相關 edges 必須降級或保持未關聯。

## 7. Weather／Side／Volatile／Transformation／Switch／Result 決策

- Weather：move 與 weather metadata 沒有直接等值證據時獨立；即使「求雨」後接「開始下雨」也只能保留弱關聯，禁止使用招式知識。
- Side／Field：effect 與 MOVE.move 完全相同時可附著；來源不明時獨立。
- Volatile：effect 與 MOVE.move 相同時可附著；counter update 與 effect end 可獨立或形成同批 batch。
- Transformation：兩階段以 actor、phase 與 absence of intervening major action 關聯。
- Switch：本身是 primary；相同 switch target 的 Ability 可附著。
- Battle Result：獨立結尾 primary；連續的 forfeit／win 只表達結果順序，不推算勝負原因。

## 8. 不足以推算 turn

目前資料沒有可靠 turn marker，且存在：

- MOVE target 全部缺失。
- 影片只保留辨識到且通過人工政策的文字事件，並非完整 battle log。
- prevented action 可能沒有對應 MOVE 事件。
- 雙打同一時段可有多個 actor／target。
- 缺少 active slots、HP、speed order、choice request、upkeep marker 與完整 source tags。
- 14 個相鄰間隔超過 5 秒，無法以固定時間窗口切 turn。

因此 1E 只輸出 timeline sequence 與 temporal group，明確禁止 `turn` 欄位。

## 9. 開源設計研究

- [Pokémon Showdown SIM-PROTOCOL](https://github.com/smogon/pokemon-showdown/blob/master/sim/SIM-PROTOCOL.md)：採用 major action／minor action 分離，以及一個 move 後可有多個 damage、status、boost、faint 的 ordered messages。Showdown 有 `[from]`／`[of]` source tags；本專案沒有時不得假造。
- [@pkmn/protocol](https://github.com/pkmn/ps/tree/main/protocol)：採用 typed object representation、exhaustive handler 與 structural verifier 的概念；不引入 TypeScript runtime。
- [@pkmn/stats](https://github.com/pkmn/stats)：借鏡 parsing 與 downstream analysis 分離；1E 只消費 frozen 1D，不回頭改 parser。
- [pkmn/engine](https://github.com/pkmn/engine)：其完整 simulator／state engine 依賴 choices、battle state 與 generation mechanics，超出 1E，故不採用。
- [Pokémon Showdown replay parser ecosystem](https://github.com/smogon/pokemon-showdown)：replay log 有完整 protocol／turn marker；本影片 OCR 不具同等證據，不能直接套用 replay reconstruction。
- [Event Sourcing](https://martinfowler.com/eaaDev/EventSourcing.html)：採用 immutable source event、deterministic projection、完整 provenance 與可重跑；不建立 state projection。
- [Event co-occurrence framework](https://arxiv.org/abs/1603.09012)：借鏡 type、time window、event parameters 與 finite-state stop condition；同時接受其核心警告：co-occurrence 只是潛在因果，不是因果證明。
- GitHub Awesome／stream processing lists（例如 [awesome-eventmodeling](https://github.com/MateuszNaKodach/awesome-eventmodeling) 與 [StreamProcessing Reading List](https://github.com/intellistream/StreamProcessing_ReadingList)）：大型 CEP、stream processor、event store 對單一 102-event deterministic batch 過重，不引入框架。

## 10. 架構結論

採用小型、deterministic 的 rule registry：每條規則定義 source／target types、time gap、required／optional metadata、stop condition、relation type、base confidence 與 ambiguity behavior。只有結構證據足夠的 relation 才把事件收進同一 Action Group；僅時間相鄰的候選保留為跨 group 的 `TEMPORALLY_ADJACENT` needs-review edge；語意衝突時不建立 relation。所有事件恰好屬於一個 group 或明確的 unlinked group，但一個事件可參與多條 relation edge，以支援 multi-target。
