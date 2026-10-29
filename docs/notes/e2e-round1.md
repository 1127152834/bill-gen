# 端到端对比（第 1 轮）：流程、发现、改动与回滚

时间：2026-10-30
目标：用 langgraph 真跑一遍生成 → 每套模板出片 → 与仓库里的参考件逐项比对。

## 怎么跑

```bash
# 1) 起服务（容器或本地都行），跑一次真实生成，把结果 JSON 存下来
curl -s -X POST http://127.0.0.1:8800/api/generations \
  -H 'Authorization: Bearer devlocal' -H 'content-type: application/json' \
  -d '{"subjectType":"personal","months":3,"accounts":{"checking":{"min":8,"max":14},"credit":{"min":6,"max":12}}}'
# 拿返回的 id 再取结果，保存成 /tmp/gen-personal.json

# 2) 全模板对比（会写 docs/e2e/report.md 与 docs/e2e/img/*-compare.png）
.venv/bin/python tools/e2e_compare.py --result /tmp/gen-personal.json --png

# 3) 只对比某几家
.venv/bin/python tools/e2e_compare.py --result /tmp/gen-personal.json --templates mercury,sofi
```

参考件映射写在 `tools/e2e_compare.py` 的 `REFERENCES`：Chase / U.S. Bank 用底稿 PDF，
Capital One / Mercury / SoFi 用官方 SAMPLE；BoA / Wells Fargo / Amex 仓库里没有参考 PDF，
改读 `layout_*.json` 做结构比对。

## 这一轮实测到的差异（及处置）

| # | 主题 | 差异 | 处置 |
|---|---|---|---|
| 1 | Mercury / SoFi / Capital One | 正文账号被掩码成 `xxxxxxxxx6431`，参考件都是**完整账号** | `ledger.AccountRef.display` 改为返回全号；需要截断的版式自己截 |
| 2 | Mercury / SoFi / Capital One / Amex | 账期被「按开户日平移」成 `Aug 29 – Sep 27`，页眉却写 `September 2026`，自相矛盾 | 新增 `CALENDAR_MONTH_TEMPLATES`：这四家走自然月，不平移（`from_game_result(..., template=...)`） |
| 3 | Mercury | 水印几乎看不见（可见像素只有参考件的 1/3） | 这三家的水印改回「最上层、极浅灰」：0.93 灰压在字上只淡一层，挪到底层会被背景卡整块盖掉 |
| 4 | Mercury | ACH 描述尾巴带 `… PAYMENT`，参考件只有对方名字 | `mercury_desc` 对 ACH/ZELLE/TRANSFER 剥尾部 `PAYMENT/PMT/DEPOSIT/TRANSFER` |
| 5 | SoFi | 出片 3 页 vs 参考 2 页 | 不是缺陷：第 3 页是 `Important Information` 法务页（参考件同样有），只是我们流水多占一页 |
| 6 | 全部 | 参考件身份泄漏检查 | 通过：`XUXING LI / GIRARD ST / SAMPLE / EXAMPLE` 均未出现 |

## 改动清单（可逐项回滚）

```bash
# 回滚整轮
git revert --no-commit <this-commit> && git commit -m "revert: e2e 第 1 轮"

# 单点回滚
git checkout <prev> -- src/billgen/synth/ledger.py            # 账号全号 + 自然月开关
git checkout <prev> -- src/billgen/themes/mercury/generate_statement.py \
                        src/billgen/themes/sofi/sofi_generate.py \
                        src/billgen/themes/capitalone/c1_generate.py   # 水印层级
git checkout <prev> -- src/billgen/synth/line_styles.py       # mercury 描述剥尾
git checkout <prev> -- tests/test_explicit_engines.py         # 基线
```

改动点：

- `src/billgen/synth/ledger.py`
  - `AccountRef.display` → 返回完整账号（原来是 `xxxx…last4`）。
  - 新增 `CALENDAR_MONTH_TEMPLATES = {mercury, capitalone, sofi, amex}`；`from_game_result(..., template="")`
    在这四家不平移账期。
- `src/billgen/cli.py`、`src/billgen/mcp/server.py`、`tools/e2e_compare.py`：把 `template` 传给 `from_game_result`。
- `src/billgen/themes/{mercury,sofi,capitalone}`：水印不再 `_wm_to_back`，改回追加在最上层（颜色 0.93 灰不变）。
- `src/billgen/synth/line_styles.py`：`mercury_desc` 剥 ACH 尾巴。
- `tests/test_explicit_engines.py`：三套 `DEFAULT_TEXT_SHA` / `DEFAULT_PIXELS` 重采（水印顺序变了）。

## 验证

- `pytest`：558 passed / 16 skipped / 33 subtests。
- 水印可见像素：Mercury 出片 56674 vs 参考 57979（修前 20012）。
- 账期：Mercury `Sep 01, 2026-Sep 30, 2026`、SoFi `09/01/2026 - 09/30/2026`。

## 第二轮补充（企业版式 + 版式漂移闸门）

- `tools/e2e_compare.py` 新增：
  - `--as-business`：离线 fixture 只有个人模型（`subjectType=business` 会在 lifeModel 被判无效，`months` 只接受 1/3/6），
    所以把同一账本改写成 `business_checking` 来跑企业版式对比；
  - 无参考 PDF 的版式改成 **layout json 位置比对**（`layout_spans()` + `compare_layout()`，基线用 `span["origin"][1]` 而非 bbox 底边）。
- 结果：`boa-personal` 版式漂移 0 段；`wellsfargo` 仅 1 段（`-` 连字符，命中了别的破折号）；企业版式三套 0 差异。
- 修的两个真问题：
  1. Chase MSF 段落金额没有千分位（`$13081.17`，而 summary 是 `$13,081.17`）→ `chase.py` 的 `msf.lowest_daily` 改用 `cents_to_grouped`；
  2. 正文句子被品牌替换改写成病句：参考件 `visit a Chase branch.`，我们出 `visit a JPMorgan Chase Bank branch.`
     → `branding.Brand` 新增 `short`（`_SHORT_NAMES` 表 + 去 `Bank`/`N.A.` 后缀），槽位模板改 `{short}`。
- 回滚：`git revert <this-commit>`；单点 `git checkout <prev> -- tools/e2e_compare.py src/billgen/branding.py src/billgen/synth/adapters/chase.py`。

## 第三轮（肉眼复核手绘版式 + 对比工具诚实化）

肉眼看的顺序：mercury → sofi → chase-business → boa-personal → amex → wellsfargo。修的都是「一眼假」的硬伤：

| 版式 | 问题 | 修法 |
| --- | --- | --- |
| amex | 水印 `SAMPLE` 0.60 灰 + 0.45 不透明度，白底上一块深灰砖头 | `WM_GRAY = (0.93,0.93,0.95)`、`fill_opacity=0.55` |
| amex | 卡行印成 `… WALGREENS #5310 AUSTIN TX AUSTIN TX` | `line_styles.amex_desc` 尾部已带城市就不再补 |
| amex | 出件日 = 期末当天（09/30 结单 09/30 寄） | `statement_date` 改期末 + 2 天 |
| wellsfargo | 封面 `Questions?` 里号码整条消失，只剩 `Available by phone …：` | 见下 |
| chase-business | MSF 段落 `$13081.17`（正文无千分位，和 summary 的 `$13,081.17` 打架） | `msf.lowest_daily` 走 `cents_to_grouped` |
| chase-business | 正文病句 `visit a JPMorgan Chase Bank branch` | `Brand.short`（真件写 `visit a Chase branch`） |

WF 号码消失的根因链（值得记一笔）：
1. 请求里没给行名 → `bridge._adopt_template_bank()` 从模板默认数据取 `name`，但模板的银行字段叫
   `phone_name`/`phone_main`/`legal_na`，不在 `_BANK_FIELDS` 里 → 拼出来的 `BankIdentity` 只有名字，
   电话/信箱全空；
2. `_common.bank_contact()` 又把 `phone_name` 映射成行名、`phone_main` 映射成空串；
3. `adapters/wellsfargo.py` 无条件 `bank.update(bank_contact(...))` → 模板里真实的
   `1-800-TO-WELLS (1-800-869-3557)` 被空串覆盖，`apply_subs` 把号码换成空 → 整行消失，
   同时 `NMLSR ID 399801`、`N.A. (115)` 也被派生随机数换掉。
修法：`real_traits(name)` 补齐身份；`bank_contact` 的 `phone_name` 取字母号；行名与模板同家时
（`same_bank`）一个字段都不动；派生值只填空、不覆盖模板真数据。

对比工具（原来会给出误导性的「0 项差异」）：
- `--templates` 里带 `alliant`/`ally` 这类**浏览器端 pdf-lib 模板**时，报「Python 侧无出片路径」而不是空原因；
- 既没有参考 PDF、也没有 layout 底稿的版式（amex）不再印「改用 layout json 做结构比对（0 段文字）」；
- `compare_layout()` 跳过孤立单字符 span（`-`/`•`）——它们和金额负号撞车，之前让 wellsfargo 恒报 1 段漂移。

回滚：`git revert fa66f48 0e2936b`；单点 `git checkout <prev> -- src/billgen/synth/bridge.py src/billgen/synth/adapters/_common.py src/billgen/synth/adapters/wellsfargo.py`。

## 第四轮（C1 户名 / 页脚地址 / 流水日期顺序）

| 版式 | 问题 | 修法 |
| --- | --- | --- |
| capitalone | 第 1 页头块只有地址，**户名整行没有**（参考件是「Jordan Smith / 118 Maple Street / Portland, OR」整块） | `adapters/simple.py::build_capitalone` 的 `address_lines` 改用 `customer_lines(ledger)`（姓名+地址） |
| capitalone | 页脚地址一直印模板默认样例地址 | 新增 `footer_address`，取持卡人地址造串（页脚不带户名） |
| usbank | 「Other Withdrawals」日期倒序：`Sep 6` → `Aug 29` → `Sep 4` | `adapters/usbank.py` 把 `checks+atm+electronic+other+fees` 拼完后按 `r.date` 排序（原来按分区拼接） |
| boa | 同类隐患：排的是格式化字符串 `"Aug 29"`，跨月 `May` 会排到 `Jun` 之后 | `adapters/boa.py` 改成按 `LedgerRow.date` 对象排序 |

U.S. Bank 的「金额列 `$` 独立成列」「负数尾随减号 `1,650.00-`」经核对是 `themes/usbank/shared.py::_fmt_amt` 有意为之（该行真件的写法就是尾随 `-`），不动。
回滚：`git checkout <prev> -- src/billgen/synth/adapters/simple.py src/billgen/synth/adapters/usbank.py src/billgen/synth/adapters/boa.py`。

## 第五轮（真调 langgraph 跑企业账本 / 修两个线上 bug）

1. **线上工具 NameError**：`POST /api/generations` 回包里 `warnings` 报
   `recommend_templates … name 'template_id' is not defined` —— `src/billgen/mcp/server.py::recommend_templates`
   把 `template=template_id` 传给 `from_game_result`，可这个函数没有这个形参。改传空模板
   （推荐本身不属于任何一家版式），并补 `tests/test_mcp.py::test_recommend_templates_runs_and_ranks`
   （之前 contract/validate/render 都有测试，只有推荐工具没覆盖）。提交 `1b1fa4e`。
2. **企业请求整单失败**：`subjectType: business` 线上恒 `GENERATION_FAILED`、`llmCalls: 1`、
   容器里连一行进度日志都没有。根因在**离线 fixture**：`persona` 节点只备了个人档案
   （`fixtures/e2e/persona.json`），企业 persona 解析必失败。修法：新增
   `fixtures/e2e/persona-business.json`、`fixtureLlm` 的答案队列支持函数条目（按调用 schema 挑 fixture）、
   补 `business.e2e.test.ts`。提交 `f67e7ca`。
   范围说明：企业**人格**已通；企业**整单**在离线 fixture 下仍受 research/lifeModel 的个人口径限制，
   企业**版式**的对比改用线上真实企业产出或 `--as-business`。
3. 企业账本真跑通后的对比（`subjectType: business`，41 笔流水 + 30 笔信用卡）：
   boa-business 版式漂移 0 段、chase-business 差异 0 项、usbank-business 差异 0 项
   （`usbank-business` 的表头 `Business Statement`、`24-Hour Business Solutions`、
   `# Items` / `Number of Days in Statement Period` 都对得上底稿）。
4. 报告分文件：`tools/e2e_compare.py --label personal|business`，两份报告互不覆盖，
   `docs/e2e/README.md` 记录重跑命令与指标口径。提交 `fae7093`。

回滚：`git revert 1b1fa4e f67e7ca fae7093`。
