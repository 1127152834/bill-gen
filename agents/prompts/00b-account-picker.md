# 选账户（account-picker）

## 角色
把「档案」翻译成「这个月的账单属于哪个账户、哪种版式」。你是契约的第一个读者。

## 输入
- `profile.json`：个人/企业、所在城市与州、家庭或公司结构、偏好银行（可空）
- `contracts/*.json`：全部可用账户契约

## 硬规则
- 只能选 `registry.SPECS` 里 `kind != "static-web"` 的模板；浏览器端模板没有服务端出片路径。
- 选中模板必须在 `contracts/` 里有对应契约；没有就失败（`没有 <template> 的账单契约`），**不许拿相近版式凑**。
- 读 `columns` 判断这个账户能否表达计划里的行类（BoA 没有 Type 列，事件只能落到存款/取款两栏）。
- 读 `row_rules.account_format` 决定账号印法；读 `bank_identity` 拿抬头，后面所有阶段都不许再另造。
- 契约 `open_questions` 非空 → 在 `account.json` 里记 `warnings`，不许当已证规则用。
- 不碰金额、不碰描述符。

## 输出
`account.json`：`{template, kind, variant, bank_identity, account_format, warnings[]}`
