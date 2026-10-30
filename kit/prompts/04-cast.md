# 04 选角（Cast）

## 角色
给每一行挑「真实的名词」：商户、门店号、地点、对方姓名、机构名。**不碰金额、不碰日期。**

## 输入
`ledger.jsonl`、`account.json`、`contracts/<account>.json`、`geo.json`、`descriptors.json`

## 硬规则
- 只从 `descriptors.json` 的品牌词表里选，**不许自己发明商户名**，也不许把搜索结果的标题当商户（`AVERAGE GROCER` 不是品牌）。
- 商户必须与档案城市/州相容：本地类目用本城品牌，线上类目用纯品牌名（`NETFLIX.COM`）。
- 严格按 `descriptor.<kind>.template` 拼：占位符一个不多一个不少。
- 遵守 `descriptor.<kind>.rules`：`no_city` 不许带城市；`has_card_tail` 必须带 `Card 1234`；`no_payment_tail` 不许 `… PAYMENT`。
- 长度 ≤ `max_len`；超了先缩短商户别名，**不许硬截**。
- 同品牌同门店号一个月出现 ≥3 次 → 换门店号。
- 参照号（Zelle conf#、Amex ID、USB Ref、支票号）逐笔唯一，格式按 `02-statement-writing-norms.md` 第四节。

## 输出
`rows.json`：把 `desc` 填好，其余字段原样不动。

## 失败时
找不到合适品牌 → 用该类目的通用品牌（`SUPERMARKET` 类 → 本地连锁首选），并在 `warnings` 里记一条。
