# Checkpoint 1J Interpretation Review and Rule Coverage Plan

## 架構邊界

1J 位於 immutable Rule Interpretation 之後。資料只能沿著以下方向流動：

```text
1H immutable Battle Facts
        +
1I immutable Rule Interpretations
        +
versioned Rule Knowledge
        ↓
1J new immutable interpretations
        ↓
separate Human Review records
```

Review 只能引用 interpretation ID 與 canonical payload hash。人工可以接受、拒絕或延後，但不能修改 certainty、conclusion、observed evidence、knowledge evidence 或 provenance。

## 執行順序

1. 稽核並提交完整 Checkpoint 1I；不 push。
2. 以 exact hash drift registry 修復既有 1G／1H historical snapshot tests，同時保留 direct frozen payload blocking gates。
3. 建立 additive `knowledge/pokemon/rules/v2/`，保留 v1 bytes、hashes 與 semantics。
4. 只從現有 213 facts 與 50 relations 選取可 deterministic 驗證的新規則。
5. 建立 18 筆分離 review records、Markdown cards 與 CSV worksheet。
6. 建立 conflict policy、coverage audit、historical drift audit、checkpoint audit 與 manifest。
7. 驗證 schemas、hash chains、determinism、transaction rollback、direct inputs 與 macOS visibility。

## Evidence-backed coverage

採用：

- 鬼火後 observed burn（temporal consistency only）
- 鬼火後 observed miss（temporal consistency only）
- 威嚇後 observed attack drop（temporal consistency only）
- 求雨後 observed rain start（temporal consistency only）
- 波動衝與明示 recoil 的 active `DAMAGE_FROM`
- 順風與明示 side-condition start 的 active `STATUS_FROM`
- 生命寶珠 activation 與同 identity self-damage 的 active `DAMAGE_FROM`
- 明示雨結束
- 明示順風結束

拒絕或延後：

- 近身戰／撒嬌 stat change：觀察與外部預期不一致，不能覆寫 Battle Fact。
- 氣象球 dynamic type：缺 target、type 與 outcome。
- burn residual parent move：人工拒絕的 temporal adjacency 不可重新包裝為因果。
- 滅亡之歌 future KO：沒有 observed KO。
- 完整 type chart：超出已有 facts。
- Levitate／Good as Gold：保留既有 unresolved，等待 target／failure／ability evidence。

## Acceptance matrix

- 8 個 1I interpretations 全部有 review cards。
- 10 個 1J expanded interpretations 全部引用既有 facts／relations。
- 每個 review record 都驗證 canonical interpretation payload hash。
- review status 與 generated certainty 分離。
- accepted unresolved 必須有 `unresolved_outcome_correct`。
- conflicted policy 保存 observed、expected、exact fields 與 evidence refs。
- v1 knowledge hashes 不變；v2 migration exact 且 additive。
- 7 個 approved metadata drift records 全部被消耗；unexpected drift 失敗。
- direct frozen 1G／1H payload 與 direct 1H input hashes 仍 blocking。
- formal output deterministic、transactional、無 hidden flag／tmp／backup／conflict dir。
