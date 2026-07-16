# Checkpoint 1D.1 — Battle Event Quality Audit

## Audit 範圍與基準

本次在修改 Parser 前逐筆檢查 `outputs/checkpoint-1d/battle_events.json` 的 102 筆事件。基準檔 SHA-256 為 `b79337bdad24dddcbfeb0e94fdb9edb3261b64fbe925988ab473d3ade4860f39`。逐筆結論保存於 `references/checkpoint1d1_quality_audit.json`，production Parser 禁止讀取該 fixture。

修改前主要數量：`MOVE=29`、`STATUS=13`、`FIELD_EFFECT=28`、`UNKNOWN_EVENT=12`。

## Quality Audit 結論

- `MOVE=29`：29 筆皆為「寶可夢使出了招式」，分類合理。
- `ABILITY=1`、`ITEM=1`：數量少是本片實際可見通知少，不是 taxonomy 缺陷。
- `STATUS=13`：過多。只有 2 筆是灼傷賦予；11 筆是灼傷造成的 damage outcome。
- `FIELD_EFFECT=28`：嚴重過多。現有規則把個體暫時效果、單側效果與形態變化混在一起，沒有一筆符合「影響整個場地的持續條件」。
- `TERRAIN=0`：本片無 accepted terrain 訊息，但類型仍有清楚獨立語意，保留。
- `UNKNOWN_EVENT=12`：全部能歸入少數穩定語意群，不需個別硬編碼 candidate。

另發現 metadata bug：`封住了對手的姆克鷹的近身戰` 因 non-greedy possessive 切割，被解析成 `target=對手`、`move=姆克鷹的近身戰`。應由通用 possessive 規則改為從最後一個「的」分隔。

## 成熟 Taxonomy 對照

[Pokémon Showdown SIM-PROTOCOL](https://github.com/smogon/pokemon-showdown/blob/master/sim/SIM-PROTOCOL.md) 已明確區分：

- `move`、`switch`、`faint` 等 major action。
- `-damage` 與 `-status`。
- `-fieldstart/-fieldend` 的全場 condition。
- `-sidestart/-sideend` 的單側 condition，例如 Tailwind。
- `-start/-end/-singleturn` 的 Pokémon volatile effect。
- `detailschange/-formechange/-mega` 的形態變化。
- `-miss/-crit/-supereffective` 等 move outcome。
- `win/tie` 等 battle result。

[`@pkmn/protocol`](https://github.com/pkmn/ps/tree/main/protocol) 將這些 protocol message 轉成 typed representation；[`@pkmn/stats`](https://github.com/pkmn/stats) 再由下游 parser／分析消費。這支持本專案繼續將 parsing 與未來 Battle State 分離。

## Unknown Analysis

| Candidate | 訊息類別 | 建議 Event Type |
|---|---|---|
| `battle_text-0011` | 幫助架勢／招式準備結果 | `MOVE_RESULT` |
| `battle_text-0013` | 效果絕佳 | `MOVE_RESULT` |
| `battle_text-0023` | 進化石與裝置反應 | `TRANSFORMATION` |
| `battle_text-0034` | 效果絕佳 | `MOVE_RESULT` |
| `battle_text-0036` | Miss | `MOVE_RESULT` |
| `battle_text-0040` | Critical hit | `MOVE_RESULT` |
| `battle_text-0054` | 進化石與裝置反應 | `TRANSFORMATION` |
| `battle_text-0089` | Recoil damage | `DAMAGE_RESULT` |
| `battle_text-0127` | HP loss，文字未明示來源 | `DAMAGE_RESULT` |
| `battle_text-0129` | Perish Song 全場單次啟動訊息 | `FIELD_EFFECT` |
| `battle_text-0172` | Forfeit | `BATTLE_RESULT` |
| `battle_text-0173` | Win | `BATTLE_RESULT` |

不建立 `MISS`、`CRITICAL_HIT`、`SUPER_EFFECTIVE` 等單筆 type；它們統一為 `MOVE_RESULT`，細節寫在 `metadata.result`。這能避免 taxonomy 爆炸，同時保留後續查詢能力。

## FIELD_EFFECT Audit

28 筆逐筆 audit 的分組結果：

- 2 筆超級進化完成訊息 → `TRANSFORMATION`
- 2 筆 Tailwind 開始／結束 → `SIDE_CONDITION`
- 12 筆 Perish count、5 筆 Protect／Encore application、2 筆 Disable application、2 筆 effect end → `VOLATILE_STATUS`，共 21 筆
- 2 筆 Disable 阻止招式、1 筆 Protect 阻擋攻擊 → `MOVE_RESULT`，共 3 筆

修改後 `FIELD_EFFECT` 應只保留 Perish Song 的全場 activation 訊息，因此本片預期為 1 筆。

## 新增類型與 ROI

新增：

- `MOVE_RESULT`：涵蓋 8 筆招式結果，避免 miss／critical 等過細 type。
- `DAMAGE_RESULT`：涵蓋 13 筆 damage outcome，將狀態本身與狀態傷害分離。
- `VOLATILE_STATUS`：涵蓋 21 筆 Pokémon-scoped 暫時效果，是修正 FIELD_EFFECT 的最大收益。
- `SIDE_CONDITION`：涵蓋 2 筆 Tailwind，與全場 effect 分離。
- `TRANSFORMATION`：涵蓋 4 筆進化啟動／完成訊息。
- `BATTLE_RESULT`：涵蓋 2 筆 forfeit／win。

暫不新增 HEAL、IMMUNITY、NOT_VERY_EFFECTIVE 等本片沒有可靠 accepted evidence 的獨立類型。`FIELD_EFFECT` 與 `TERRAIN` 保留，因為成熟 protocol 有明確全場語意，但不為目前零或一筆樣本過度擴充規則。

## 預期 Regression

| Event Type | 修改前 | 建議修改後 |
|---|---:|---:|
| `MOVE` | 29 | 29 |
| `STATUS` | 13 | 2 |
| `ITEM` | 1 | 1 |
| `ABILITY` | 1 | 1 |
| `WEATHER` | 2 | 2 |
| `FIELD_EFFECT` | 28 | 1 |
| `UNKNOWN_EVENT` | 12 | 0 |

總事件數必須保持 102；分類改善不得改動 candidate、時間、原文或 Checkpoint 1C provenance。

## Stopping Decision

完成本輪後，現有 102 筆 accepted evidence 已有明確 type，且 `FIELD_EFFECT`、`STATUS` 不再承擔過廣語意。繼續針對本片細分 `critical`、`miss`、各種 damage cause 或為尚未出現的 heal／immunity 建立更多 type，會增加 schema 與下游分支成本，卻沒有新的實際 evidence 支撐，工程 ROI 偏低。

建議在人工抽查 1D.1 regression mapping 後停止 taxonomy 微調，下一個 Goal 應直接使用 BattleEvent IR；若未來影片出現穩定的新訊息族群，再以同一 audit → registry → regression 流程擴充，而不是預先追求完整百科式 taxonomy。
