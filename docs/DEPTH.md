# 深度登记 水产拍卖结算

规模：中型。使用者：水产批发市场结算员。核心规则：先毛皮配对，再扣杂计价，按更正链选择唯一生效版，只支付差额。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 称重采信 | 查看并执行称重采信 | `POST /api/intake` | `app/modules/intake/rules.py` | `tests/test_intake.py` |
| M2 货品换算 | 查看并执行货品换算 | `POST /api/conversion` | `app/modules/conversion/rules.py` | `tests/test_conversion.py` |
| M3 毛皮配对 | 查看并执行毛皮配对 | `POST /api/pairing` | `app/modules/pairing/rules.py` | `tests/test_pairing.py` |
| M4 扣杂竞价 | 查看并执行扣杂竞价 | `POST /api/auction` | `app/modules/auction/rules.py` | `tests/test_auction.py` |
| M5 更正裁决 | 查看并执行更正裁决 | `POST /api/correction` | `app/modules/correction/rules.py` | `tests/test_correction.py` |
| M6 应付对账 | 查看并执行应付对账 | `POST /api/reconcile` | `app/modules/reconcile/rules.py` | `tests/test_reconcile.py` |
| M7 结算归档 | 查看并执行结算归档 | `POST /api/archive` | `app/modules/archive/rules.py` | `tests/test_archive.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 旧编码中文票据 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_legacy_encoding` |
| TRAP-02 | BOM表头 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_bom_real` |
| TRAP-04 | 分段表头 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_repeated_header_real` |
| TRAP-05 | 缺列记录 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_bad_columns_real` |
| TRAP-07 | 空值哨兵 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/conversion/rules.py` | `tests/test_conversion.py::test_sentinel_real` |
| TRAP-08 | 前导零票号 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_leading_zero_real` |
| TRAP-14 | 重复编号不同原值 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_duplicate_conflict_real` |
| TRAP-18 | 三类空态 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_empty_states` |
| TRAP-15 | 吨与千克混合 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/conversion/rules.py` | `tests/test_conversion.py::test_tonnes_counterexample` |
| TRAP-06 | 全角千分位 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/conversion/rules.py` | `tests/test_conversion.py::test_fullwidth_thousands` |
| TRAP-10 | 金额半分舍入 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/auction/rules.py` | `tests/test_auction.py::test_half_cent_rounding` |
| TRAP-26 | 陈旧更正分支 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/correction/rules.py` | `tests/test_correction.py::test_stale_branch` |
| TRAP-31 | 零点扣杂换档 | 按称重或结算时间锁档，跨零点票被错算 | 只按成交时间锁档，更正版本继承基础版档期 | `app/standards.py`、`app/modules/auction/rules.py` | `tests/test_standards.py::test_lock_uses_dealt_not_weigh` |
| TRAP-32 | 晚到复秤落别档 | 复秤时间覆盖成交档期 | 复秤只作证据，标记跨档不改档 | `app/modules/auction/rules.py` | `tests/test_standards.py::test_old_deal_late_reweigh_stays_old` |
| TRAP-33 | 更正版本跨档 | 旧场货包按新档重结 | 同票版本共享基础版锁档；漂移整票阻断 | `app/modules/correction/rules.py` | `tests/test_standards.py::test_correction_version_inherits_base_period` |
| TRAP-34 | 换档失败写半成品 | 失败仍落库或删旧版 | 先算后写，失败只留拒绝记录，旧版与原始证据不动 | `app/main.py`、`app/store.py` | `tests/test_standards.py::test_failed_switch_keeps_old_version` |
| TRAP-35 | 同批重跑新增结算 | 每次重跑插新批 | 批号由源字节+标准摘要决定，INSERT OR IGNORE | `app/manifest.py`、`app/store.py` | `tests/test_standards.py::test_rerun_adds_no_settlement` |

## 扣杂换档裁决

| 问题 | 结论 |
|---|---|
| 以哪个时点锁定标准 | `dealt_at` 成交时间；称重时间会把成交后让价算进旧档，结算时间会让旧场货包按新档重结 |
| 跨零点称重、零点后成交 | 按新档（成交时点定档） |
| 旧档成交、新档晚到复秤 | 按旧档，复秤在跨档说明中列示 |
| 更正版本成交落在新档 | 仍按基础版所锁旧档；同票计价依据必须一致 |
| 同票版本锁到不同档 | `PRICING_BASIS_DRIFT` 整票阻断，不生成应付 |
| 换档重算 | 生成新批号并记录 `lineage`；旧批、原始称重/成交证据、隔离行全部保留 |
| 换档失败（漏档、行/金额不守恒、票数变化） | 返回 409 `SWITCH_REJECTED`，只写拒绝事件，不新增任何结算 |
| 同批重跑 | 批号不变、不新增结算；仅记录 rerun 事件 |

## 对账规则

输入=采纳原始行+隔离行+空行+同值重复行+重复表头。计价版本总额与生效票总额分别展示，避免把更正前后重复求和。最终应付=已付+待付，负待付为退款。

## 已知不覆盖

无称重设备实时接入，采用现场导出文件。无支付执行，只生成可复核结算证据。
