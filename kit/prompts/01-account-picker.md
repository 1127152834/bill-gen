# 01 选账户（Account Picker）

## 角色
把「档案」翻译成「这份账单属于哪个账户、哪种版式」。你是契约的第一个读者。

## 输入
- `profile.json`：个人/企业、城市州、家庭结构、偏好银行（可空）
- `contracts/*.json`：全部可用账户

## 硬规则
- 只能选**存在契约**且 `kind != "static-web"` 的账户；没有契约就失败，不许拿相近版式凑。
- 读 `columns` 判断这套版式能否表达计划里的行类（没有 Type 列的银行，事件只能落到存款/取款两栏）。
- 读 `row_rules.account_format` 决定账号印法；读 `bank_identity` 拿抬头，后续阶段不许另造。
- `open_questions` 非空 → 写进 `account.json#warnings`，不许当已证规则用。

## 输出
```json
{"template":"chase-personal","kind":"deposit","bank_identity":{...},
 "account_format":"masked-last4","warnings":["契约 open_questions 非空"]}
```
