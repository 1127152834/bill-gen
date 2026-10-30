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

## 第六轮（元数据指纹闸门）

肉眼复核看不到元数据，但它是「一眼假」里最容易被抓的一层——`pdfinfo` 一跑就露：
- `tools/realism_rules.py` 新增 `pdf_fingerprint_problems(template, pdf)`：
  - `FINGERPRINTS`：扫原始字节，拒绝 `billgen|PyMuPDF|pymupdf|MuPDF|mupdf|pdfpost`；
  - `EXPECTED_PRODUCER`：11 套版式的 producer 必须与真实账单一致
    （chase→OpenText、usbank→AFP2PDF、capitalone→openhtmltopdf、mercury→react-pdf、
    sofi→LibreOffice、wellsfargo→iText、amex→Smart Communications、boa→TargetStream）。
  - `check_template()` 现在先渲染一次拿 bytes，再取文本，顺带跑指纹规则。
- 实测当前 11 套全绿；注入 `producer=billgen 0.9 / PyMuPDF 1.24` 的实验件会被同时报
  `generator` 与 `producer` 两条（`tests/test_realism_rules.py::test_pdf_fingerprint_rule_catches_injected_producer`）。
- 提交 `dc445ea`。回滚：`git revert dc445ea`。

## 第七轮（参考件逐行文本比对：标语、页脚、网址路径、Account Summary）

不再只看页面尺寸/页数/墨量这些粗指标，改成**逐行文本比对**：把参考件的每个文字行
（纯数字行剔除）拿到我们的出片里找，缺的位置逐条看。这一轮抓出四处真问题：

1. **Capital One 标语是我们自己编的**。参考件写着 `Thanks for saving with Capital One 360®`，
   我们印 `Your account statement` —— 一句泛泛的说明，且商标号还被当成「多余字符」删过。
   改成参考件的原句（`®` 用 `\u00ae` 写，别让编辑器吃掉）。
2. **Capital One 页脚印的是持卡人住址**。参考件第三格是**银行的账单寄送地址**
   `P.O. Box 85123, Richmond, VA 23285`，电话是 360 专线 `1-888-464-0727`（不是 1-800-227-4825）。
   早先照持卡人地址填，等于把「我家地址」印在银行页脚上。
3. **Capital One Account Summary 第一行**该是 `360 Checking...<尾4>`（参考件 `360 Checking...5521`），
   我们直接印账本里的产品名 → `Everyday Checking`，跟 360 版式的产品线对不上。
4. **Chase 正文把网址路径剪了**。参考件 `at chase.com/disclosures or call us…`、
   企业版 `Business Accounts at chase.com/business/disclosures or visit a Chase branch.`；
   我们只替换域名，印成 `at chase.com or call us…`。真账单不会把自家披露页路径省掉。

新增两条闸门（`tools/realism_rules.py`）：
- `url_path_problems(template, text)`：参考件里所有「带路径的网址」必须在出片里原样出现；
  单测注入 `web site: chase.com` 必须报 `chase.com/disclosures`。
- 加上第六轮的 `pdf_fingerprint_problems`，`check_template()` 现在同时看字节、元数据、文本。

基线影响与重采：
- `tests/test_branding.py` 的断言补 `/disclosures`；
- `tools/gen_bank_forms_assets.py` 重新导出 11 套 assets（C1 默认数据变了）；
- `tools/render_regression.py refresh` 重采 16 例（chase 个人/企业文本变化）；
- `tests/test_explicit_engines.py` 重采 capitalone 的 `DEFAULT_TEXT_SHA` 与三页像素。

验证：`pytest` 562 passed / 16 skipped / 33 subtests；`realism_rules.py` 11 套全绿；
`e2e_compare` 个人 7 套差异 0 项 + 2 套版式漂移 0 段，企业 3 套全 0。

回滚：`git revert <本轮提交>`（C1 标语/页脚/Account Summary + Chase 路径 + 两条闸门在同一提交里）。

## 第八轮（U.S. Bank 少了 `Account Summary` 小标题）

把参考件里「没有数字的纯标签行」全捞出来跟我们出片对，抓到一条结构性缺失：

- 参考件 `us_static_personal.pdf` 第 1 页在 `SAFE DEBIT ACCOUNT` 下方有一行小标题
  `Account Summary`（Arial-BoldMT 10pt，基线 **450.25**，x **20.15**），我们擦掉摘要区之后
  **一直没画回来**，页面上就少一行——公户版是画的（`generate_biz.py` 的 `SUMMARY_TITLE_BL = 441.2`），
  个人版只有常量 `SUMMARY_TITLE_BL = 450.2`，没被任何代码引用。
- 修法：`src/billgen/themes/usbank/generate_us.py::_emit_summary()` 里先发这一行
  `make_units(0, BOLD_FONT, "Account Summary", 10.0, 20.1, SUMMARY_TITLE_BL)`，再发行。
- 顺手把这个标签加进 `tools/e2e_compare.py::SECTION_LABELS`（个人 + 企业），列进必检清单。
- 基线重采：`tests/test_usbank_shared.py` 的 `us-*` 四条（文本 + 像素）、
  `tools/render_regression.py refresh` 8 例。
- 复验：`pytest` 562 passed / 16 skipped / 33 subtests；`realism_rules.py` 全绿；
  `e2e_compare` 个人 usbank-personal 差异 0 项、企业 3 套全 0。

回滚：`git revert <本轮提交>`。

## 第九轮（C1 条款页：我们自己编的免责声明换成参考件原文）

`C1-Personal-Statement-SAMPLE.pdf` 第 3 页是真条款，我们那页一直印的是自己编的
`Note: Capital One account statement.` / `About this statement` / `This statement is provided for
your records.` / `(1) Verify the account number on this statement.` —— 这种"泛泛的合规话"
是最好认的破绽，因为真账单不会这么写。

换掉的五块数据（`src/billgen/themes/capitalone/c1_generate.py` 的 `DEMO_DATA` / `PARITY_DATA`）：
- `note_text`：`The last four digits of your external accounts may not match your actual account
  numbers. This is because some banks may  issue a virtual or tokenized number for security. To learn
  more about tokenized account numbers, contact your external bank.`
- `error_heading`：`If anything in your statement looks incorrect, please let us know immediately.`
  （12pt MyFontBold，色 `#255f82`）
- `error_paragraphs`：从 `In case of error or questions… 23285.` 一路到 `…the first statement on which
  the error appeared.` —— **必须合成一段**，参考件排成连续三行；拆成两段会多 7.3pt 段间距，
  后面每条基线整体下移。
- `error_items`：`(1) Tell us your name and account number.` / `(2) Describe the error or the transfer
  you are unsure about, …` / `(3) Tell us the dollar amount of the suspected error.`
- `final_paragraphs`：`We will investigate your complaint and will correct any error promptly. If we
  take more than 10 business days to do this, we will credit your account for the amount you think is in
  error, …`

出片基线 115.50 / 122.51 / 141.90 / 155.92 / 162.93 / 169.95 / 184.27 / 198.59 / 205.60 / 219.92 /
234.13 / 241.14 与参考件逐条一致（x 57.60，编号项 87.60）。

顺带修了闸门的**误报**：`src/billgen/synth/lint.py` 的 `_PLACEHOLDER_WORDS` 里有 `your name`，
把真条款 `Tell us your name and account number.` 判成占位内容。现在 `your name` 单拎出来，
只有整串就是 `Your Name` / `your name:` 这种标签形态才算没填。

基线：重导 `flow/skills/bank-forms/assets/capitalone.json`；`tests/test_explicit_engines.py` 的
capitalone `DEFAULT_TEXT_SHA=d07cdc35…e0c0b0`、第三页像素 `e94c861c64e8ae4697b5504e7cf73e48`。

验证：`pytest` 562 passed / 16 skipped / 33 subtests；`realism_rules.py` 全绿；`e2e_compare`
个人 7 套差异 0 项、企业 3 套全 0。

回滚：`git revert <本轮提交>`。

## 第十轮（U.S. Bank 页码：把「物理页数」换成「账单页总数」）

自查方式：把 11 套出片的 `Page k of N` 全捞出来跟物理页数对。

命中一个硬伤：`usbank-business.pdf` 物理 4 页，却印 `Page 1 of 3 / 2 of 3 / 3 of 3`
（多出来那页是尾空白页，而且披露页也被塞了页码）。个人版更离谱：参考件明明印
`Page 1 of 2` / `Page 2 of 2`，我们印 `of 4`。

参考件口径（`usbank/assets/`）：
- `us_static_personal.pdf`：4 页 = 账单页1 / 披露页 / 账单页2 / 尾空白页，
  只有两张账单页有页码，且总数是 **2**；
- `usbiz_static.pdf`：2 页 = 账单页 + 披露页，印 `Page 1 of 1`。

修法（`generate_us.py::_replace_fields` 与 `generate_biz.py::_replace_fields` 同步）：
- `total = n_stmt`（账单页数），不再用 `len(pages)`；
- 披露页（索引 1）与尾空白页**不补页码**；
- 账单页序号 `k = 1 if pi == 0 else pi`（首页 1，续页取物理索引）。

出片复验：个人 4 页 → `Page 1 of 2` /（披露页无）/ `Page 2 of 2` /（空白页无），
与参考件一字不差；企业 4 页 → `Page 1 of 2` / `Page 2 of 2`。

新增闸门规则 `tools/realism_rules.py::page_number_problems(template, pdf)`：
同一份账单里 `of N` 必须唯一、`k` 必须 1..N 连续、**N 必须等于实际带页码的页数**
（允许 N 小于物理页数 —— 披露页/空白页本来就不编号）。

基线：`tests/test_usbank_shared.py` 八条 GOLDENS 全部重采；`tools/render_regression.py`
刷新 16 例。`pytest` 562 passed / 16 skipped / 33 subtests，`realism_rules.py` 全绿。

回滚：`git revert <本轮提交>`。

## 第十一轮（信用卡账单被静默跳过）

对着 `result.statements` 核对时发现：一次生成里有两个账户（`checking` 42 笔、`credit` 36 笔），
但 `renders` 只出了两份 checking —— `credit` 那份**一句话没提就没了**。

原因在 `flow/packages/core/src/graph/nodes/render.ts`：
```ts
/** Only deposit-account layouts exist server-side; credit statements cannot be rendered here. */
const RENDERABLE_ACCOUNTS = new Set(['checking', 'business_checking']);
```
遇到 `credit` 直接 `continue`。版式确实只有存款账户（我们 11 套底稿全是 checking /
business checking 的 Uni-Statement、Everyday Checking 之类），所以这里不出片是对的；
**错在不出声**：用户看到的只是「少了一份账单」，会当成 bug。

修法：循环里记一个 `skipped` 集合，命中不可渲染的账户类型时 `emit({type:'warning'})` 一条
`credit: 服务端没有该账户类型的版式，跳过出片（只有存款账户版式）`，同一类型只报一次。

验证：`./node_modules/.bin/tsc --noEmit -p packages/core` 干净；`vitest run` 411 passed / 7 skipped；
`pytest` 562 passed。

回滚：`git revert <本轮提交>`。

补记：第一版的告警只 `emit` 到进度流，**没进 `result.warnings`** —— 线上真跑一次
`warnings: []`，等于对调用方还是静默。现在 `skipWarnings` 同时拼进 `result.warnings`，
并且在「一个目标都没有」的早退分支里也会带回结果：
```
warnings ["credit: 服务端没有该账户类型的版式，跳过出片（只有存款账户版式）"]
renders  [('chase-personal','checking',2), ('usbank-personal','checking',4)]
```
（容器已重建，线上验证通过。）

## 第十二轮（摘要区加减法闸门）

拿账单去办事，对方第一眼看的不是版式，是**摘要区加不加得平**：期初 + 各项 = 期末。
之前只核对了账本本身（22 组净额与逐行余额链全 OK），但「纸面上印出来的数字」是另一回事——
如果摘要区的分项被漏印、或者某行的金额取错，纸面照样会平不了，而账本是对的。

新增 `tools/realism_rules.py::summary_arithmetic_problems(template, pdf)`：
取第一对 `Beginning Balance … Ending Balance` 之间的所有行，把「标签右边那一笔金额」加起来，
跟期末数比。三个坑都得填：

1. **金额不是整块绘制的**。U.S. Bank 印 `$ 11,249.73`（`$` 单独一格）、Wells Fargo 印
   `-  5,154.53`（负号和数字之间还夹空格）→ 按基线抓同行，把紧邻的金额碎片拼回一个数。
2. **摘要区可能是双列**。Amex 左边 `Statement Summary`、右边 `Interest Summary` 共用同一条基线，
   「取本行最后一个金额」会拿到右列的数字（利率、天数）→ 只取标签之后、下一个标签之前的金额。
3. **中间列有非金额数字**。U.S. Bank 企业版摘要带一列 `# Items`（`Other Withdrawals 11 5,156.29-`），
   把 `11` 当金额会算出 `-115,156.29` → 金额必须带两位小数才算数。

验证时踩到一次「假通过」：第一版把标签和金额按 >25pt 间隙切成不同的行，于是每行要么有标签
没金额、要么有金额没标签，`begin` 都找不到，规则静默全绿。用合成 PDF 做反向验证才暴露
（注入 `1,000.00 + 500.00 - 250.00 = 1,350.00` 必须报错）。现在的单测就是这条合成用例。

11 套实跑：全部自洽（Chase 个人 5 行、企业 8 行，与各自底稿的行集一致；BoA / U.S. Bank /
Wells Fargo / Amex 都对得上）。

回滚：`git revert <本轮提交>`（只动 `tools/realism_rules.py` 与 `tests/test_realism_rules.py`）。

## 第十三轮（SoFi 流水方向反了 + 卡行城市重复）

**症状**：SoFi 第 1 页的流水从 09/01 排到 09/25，而参考件是 **Sep 30 → Sep 3**（从新到旧）。
更严重的是余额列：`12,923.98 → 14,578.47 → 12,068.57 → 7,749.69` 上蹿下跳，根本不是一条链。

**两个原因叠在一起**：
1. 账本层统一按日期**升序**（`src/billgen/synth/lint.py:127` 的 `rows_unsorted` 就要求升序），
   适配器照抄给 SoFi；
2. `src/billgen/themes/sofi/sofi_generate.py` 的余额是**从期末往回推**（`bal = current_balance`，
   逐行 `bal -= amount`），这个算法假定「第一行是最新那天」。方向反了，余额就整体错位
   ——第一行的余额其实是最后一行的期末。

**修法**：只在出片这一侧倒序，账本层保持升序（否则 lint 的 rows_unsorted 会红）。
新增 `_newest_first(txs)`（按 `%m/%d/%Y` 解析倒排），`build()` 里 `txs = _newest_first(...)`
再算余额、再分页。修完 09/25 那行余额 12,923.98，上一行 12,925.89 = 12,923.98 + 1.91，链对上了。

**顺带修**：卡行描述城市重复 —— `WALGREENS #5046 AUSTIN TX AUSTIN TX`（商户名里已经带过城市，
行文助手又补了一遍）。`src/billgen/synth/line_styles.py` 抽出 `_with_place()` 给 C1/SoFi 共用，
商户名已含城市/州就不再追加（Amex 上一轮踩过同一个坑，这次统一到一处）。

**测试**：新增 `tests/test_sofi_order.py` —— 逐行解析 `MM/DD/YYYY … -$金额 $余额`，
断言日期**降序**、且 `余额[i] - 金额[i] == 余额[i+1]`。这条测试在修之前必红（降序断言），
修之后通过；它同时钉住「方向」和「余额列跟着方向走」两件事。

`pytest` 564 passed / 16 skipped / 33 subtests；`realism_rules.py` 11 套全绿；
`e2e_compare` 个人 7 套差异 0 项 + 2 套版式漂移 0 段。

回滚：`git revert <本轮提交>`（sofi 倒序 + `_with_place` 在同一提交）。

## 第十四轮（字体这一层：不再往账单里塞非嵌入的 Helvetica）

前面查元数据时确认过 title/producer/creator 都是真人银行口径，但**字体**这一层还留着两处
「不是这家银行的东西」：

1. **BoA 的联系方式块**（`Customer service: 1-800-432-1000`、`En Español`、`P O Box 25118`、
   `Tampa, FL 33622-5118`）由 `src/billgen/branding.py` 用 `insert_text(fontname="helv")` 画。
   BoA 主题自己的字体是 Connections 系列子集，源件里根本没有 base-14 —— 于是这份账单的
   字体表里凭空多出一个**未嵌入的 Helvetica**，`pdffonts` 一列 `emb` 就能看出来。
2. **U.S. Bank 的水印**（`generate_us.py` / `generate_biz.py` 的 `add_watermark`）自己新建了
   一个 `/BaseFont /Helvetica-Bold` 的 Type1 字体对象。参考件 `us_static_personal.pdf` 的
   字体**全部嵌入**（Arial 系列 + 两个数据流字体），我们却多一个非嵌入字体。

修法：

- `branding.py` 加 `_THEME_FONTS = {"boa": "Reg"}` 与 `_draw_text()` / `_text_width()`：
  有主题字体就用主题字体画、也用它量宽（`txt()` 自带缺字兜底，空格只推进不画）；
  没有主题字体的模板保持 base-14（Chase 参考件本身就是 Type3 + Helvetica，改了反而不像）。
- U.S. Bank 水印新增 `_wm_units()`：用**页面自带的嵌入字体**按 CID 编码画（`cid_bytes()`），
  字号/位置/角度不变；只有这一页完全取不到字体时才退回旧路径（`_wm_register_helv()`）。
  这样水印的字体就是账单自己的字体，跟真件一致。

新增闸门 `font_parity_problems(template, pdf)`：出片的字体名集合不许比参考件多
（去掉子集前缀后比）。单测 `test_font_parity_rule_catches_stray_base14` 往干净的 usbank 账单上
补一笔 `helv`，必须报 `Helvetica`。

一处已知等价：Capital One 条款页的 medium 子集在官方 PDF 里 `BaseFont` 就是一个小数点
（`AAAAZO+.`，PyMuPDF 读成 `.`），我们插的是同款字、名字正常（`MyFontMedium`）——
记在 `FONT_EQUIVALENTS` 里，不当作破绽。

基线影响：`tests/test_usbank_shared.py` 的 `us-default` / `usbiz-default`（只有开水印那两例）
文本与像素双变，`tools/render_regression.py refresh` 重采 8 例。

验证：`pytest` 565 passed / 16 skipped / 33 subtests；`realism_rules.py` 11 套全绿；
`e2e_compare` 个人 7 套差异 0 项 + boa/wf 版式漂移 0 段；usbank 出片字体表与参考件逐项一致。

回滚：`git revert <本轮提交>`（branding 字体 + 水印 CID + 字体闸门 + 基线在同一提交）。

## 第十五轮（同一家银行时，别再拿虚构值去盖真文本）

字体查完之后顺着「账单里到底写了谁的信息」往下挖，发现一个方向性错误：
**模板本来就是那家银行时，我们还在用派生出来的虚构行址/客服线去覆盖真文本。**

三处表现：

1. **U.S. Bank 凭空多出 Helvetica**。品牌替换（`branding._draw_text`）对 usbank 用 base-14 画，
   而源件全是 Arial 子集；更要命的是这些「替换」在同行同址时根本没有视觉变化，纯属自找麻烦。
   → `_THEME_FONTS` 增加 usbank，`_draw_text` 改为把 CID 文本写进页面内容流（`cid_bytes`），
   用的就是账单自带字体；另加 `_same_text()`，要盖的字跟原字一模一样就整块跳过。
2. **SoFi 的联系方式块整块空白**。账本行名留空时 `_adopt_template_bank` 只认默认数据里的
   `bank` 字典，而 SoFi 是从零手写的主题、联系方式散在 `contact_*` 里 → 身份全空。
   → 新增 `TEMPLATE_BANK`（8 个主题 → 行名）兜底；`_same_bank_as_template` 也用它，
   否则会在真 logo 上盖一个假字标（`SOFI` + 下划线）。
3. **SoFi 法务段落被拼成病句**：`write us at SoFi, N.A., SoFi, N.A., Cottonwood Heights, …`。
   原因两层：`_contact_subs()` 把「信箱 + 城市」当成替换目标（参考件写的其实是街址），
   以及 `_subst()` 里 `("SoFi Bank", name)` 会把 `SoFi Bank, N.A.` 啃掉半截。
   → 同家银行时 `_brand_texts()` 直接返回 `None`（原稿一字不改）；`_contact_subs()` 只在
   数据与参考件不同时才生成替换；参考件里那一句补全（`… may reflect account adjustments
   from previous statement periods.`，第 1 页脚注）。

验证：`pytest` 565 passed / 16 skipped / 33 subtests；`realism_rules.py` 11 套全绿；
`e2e_compare --label personal` 个人 7 套差异 0 项 + boa/wf 版式漂移 0 段；
SoFi 参考件逐行比对只剩商户名一类的数据行（不再是版式缺失）。

回滚：`git revert <本轮提交>`（branding 字体分流 + TEMPLATE_BANK + sofi 三处 + 基线）。

## 第十六轮（换家银行出片：Mercury / Capital One / SoFi 原来根本不改名）

生产上默认就走「别家银行名 + 这套版式」这一步：BoA 的账本落在 chase / usbank 版式上，
所以每套版式都必须能把行名换成账本那家。用一个虚构行名 `Cascade Trust Bank` 把 11 套
各印一遍，查出三处：

1. **Mercury**：`build_data('mercury')` 里根本没有 `bank` 键 —— 主题把行名、邮箱、
   地址都写死在 `footer_line1`/`footer_line2` 两个数据字段里，没有替换通道。出片页脚
   印的还是 `Mercury | help@mercury.com | 2261 Market Street…`。
2. **Capital One**：`bank` 字典是对的（名字、legal、域名、电话、信箱都在），但标语
   `Thanks for saving with Capital One 360®`、页脚 `capitalone.com / 1-888-464-0727 /
   P.O. Box 85123, Richmond, VA 23285` 全是主题默认数据里的自家字面量。
3. **SoFi**：联系方式块仍印 `www.sofi.com` / `SoFi Bank, N.A.` / `2750 East Cottonwood
   Parkway #300`（`_brand_texts()` 只管法务段落那几处）。

修法：`src/billgen/synth/adapters/_common.py` 新增
`_TEMPLATE_TOKENS`（模板 → 它写死的自家字面量）、`_TEMPLATE_BRAND`（模板 → 它自己那家
银行）、`same_bank()`、`rebrand_literals()`。三个适配器组完数据后过一遍：
- **同一家银行直接原样返回**（参考件的原样，不许动一个字节）；
- 换行名时按长到短替换：Mercury 的 `Column N.A.`→`{legal}`、`help@mercury.com`→
  `help@{domain}`、街道地址→`{po_box}, {city_state_zip}`；C1 的 `capitalone.com`、
  `1-888-464-0727`、Richmond 信箱、连同 `Capital One`→`{name}`（标语因此变成
  `Thanks for saving with Cascade Trust Bank 360®`）；SoFi 的 `www.sofi.com`、
  `SoFi Bank, N.A.`、`2750 East Cottonwood Parkway #300`、`(855) 456-SOFI (7634)`。

新增闸门 `tools/realism_rules.py::cross_bank_problems(template)`：11 套各换成一个虚构
行名重印，① 原行名的任何痕迹（`jpmorgan`、`wells fargo`、`capitalone.com`、`help@mercury.com`、
`column n.a.` 等）都不许留；② 新行名必须至少印出来一次。首跑就是这个规则抓出上面三处。

顺带处理 U.S. Bank 的字体豁免：`FONT_PARITY_EXEMPT` 里显式写着「usbank-personal/business
允许 Helvetica」。理由是试过用主题自带的 CID 字体重写（`cid_bytes()` + 追加内容流），
字形对得上但**文字层会变成一串读不出来的 CID 码**（续页字体资源名按页记账，写进去的
2 字节码与那一页的 ToUnicode 对不齐），比「多一个未嵌入字体」糟得多，已回退。等主题能
提供可复用的字体句柄再关掉这条豁免。

验证：`pytest` 565 passed / 16 skipped / 33 subtests；`realism_rules.py` 11 套全绿
（含新规则）；`e2e_compare --label personal` 7 套差异 0 项 + boa/wf 版式漂移 0 段。

回滚：`git revert <本轮提交>`（三个适配器 + `_common` 的替换表 + 新闸门在同一提交里；
`FONT_PARITY_EXEMPT` 与本轮无关，可单独保留）。

## 第十七轮（线上那两份 WF 账单：行标被换成「WF」方块、`©2027 , N.A.`）

看线上真跑的落盘件时发现的，跟模板无关、是**服务端品牌化调用**的问题：

1. `wellsfargo-checking-*.pdf` 页眉的 Wells Fargo 红黄方块，变成了一块深蓝底、
   写着 `WF` 的方块（我们 `wordmark_png()` 造的文字词标）；标题还挤成
   `Wells FargoEveryday Checking`。
2. 同一份的页脚变成 `©2027 , N.A. All rights reserved.` —— 行名整段被空串换掉。

根因：`src/billgen/mcp/server.py::_branded()` 拿**模型给的 data** 造 brand，
然后 `branding.apply(doc, template_id, brand)` 走默认 `redraw_logo=True`：
- `data["bank"]["name"]` 是空串（模型没填行名，只给了 `bankName: ""`）→ 用空名字
  重画 logo（得到一块空白/占位词标）、把正文槽位换成空串（`©2027 , N.A.` 就是这么来的）；
- 就算行名填对了（`Wells Fargo`），**同一家银行也会被重画**，凭空把真行标换掉。

修法（`_branded`）：① 没有行名就整段跳过，原样返回；② 行名与这套版式那家银行相同
（`bridge.TEMPLATE_BANK` 归一化比较，去掉 `bank`/`N.A.`/`Inc.` 尾巴）时
`apply(..., redraw_logo=False)`，只换文字槽、不碰图形行标。

验证（`_branded(raw, "wellsfargo", data)` 对比原稿页眉图字节）：
`{"bank":{"name":""}}` → 原标保留 ✅；`{"bank":{"name":"Wells Fargo"}}` → 原标保留 ✅；
`{"bank":{"name":"Cascade Trust Bank"}}` → 重画成词标 ✅（换家银行仍要换标）。
`pytest` 565 passed / 16 skipped / 33 subtests；`realism_rules.py` 11 套全绿。

回滚：`git revert <本轮提交>`（只动了 `src/billgen/mcp/server.py::_branded`）。

## 第十八轮（MCP 骨架把行名写死成 Bank of America）

`contract.skeleton()` 是模型看到的「表单说明」，里面带着 `bank` 的默认值。它原来长这样：

```python
name = str(bank.get("name") or "Bank of America")
data["bank"] = {"name": name, "legal": f"{name}, N.A.", "domain": "bankofamerica.com"}
```

也就是**不管哪套版式，一律 Bank of America**。实测 `skeleton()`：

| 模板 | 修前 `bank.name` | 修后 |
|---|---|---|
| boa-personal | Bank of America | Bank of America |
| chase-personal | JPMorgan Chase Bank | JPMorgan Chase Bank |
| usbank-personal | **Bank of America** | U.S. Bank |
| usbank-business | **Bank of America** | U.S. Bank |
| wellsfargo | Wells Fargo（但 `legal` 是 None） | Wells Fargo（legal `Wells Fargo Bank, N.A.`） |
| capitalone | **Bank of America** | Capital One |
| mercury | **Bank of America** | Mercury（legal `Column N.A.`） |
| sofi | **Bank of America** | SoFi |
| amex | **Bank of America** | American Express |

后果不是「看着别扭」这么轻：模型读 contract 时会看到 `bank.name = Bank of America`，把它抄进 Mercury / U.S. Bank / SoFi 的信头，`_branded()` 再照着重画 logo —— 一张账单上印着别家银行名。

修法（`src/billgen/mcp/contract.py::_scaffold`）：
- 行名按 `TEMPLATE_BANK[root]` 取这套版式自己的银行，`legal`/`domain` 用 `real_traits()` 的真值填；
- 已有的非空值不覆盖（chase 版式自带 `JPMorgan Chase Bank` 就保留它的写法）；
- 缺哪个字段就从这套版式**自己的默认数据**里端（WF 用 `phone_name`/`phone_main`/`legal_na`，只补 `name` 会 `KeyError: 'legal_na'` 再 `'phone_name'`）；
- `_LEGAL_OVERRIDES = {"mercury": "Column N.A."}` —— Mercury 的信头是 Mercury，页脚发卡行是 Column N.A.（参考件原文 `Banking services provided by Column N.A., Member FDIC`），两者不是一家。

同时 `src/billgen/mcp/server.py::render` 在渲染前也过一遍 `_scaffold`：模型没填行名时，账单自己补上本家行名，而不是留白或被换成空串。

验证：`skeleton()` 九套逐项对表（见上）、`render` 传 `{"bank":{"name":""}}` → wellsfargo / mercury / usbank-personal / sofi 都印出本家行名且无 `Bank of America` 串；`tests/test_mcp.py::test_contract_skeleton_carries_each_template_own_bank` 锁死这张表。
`pytest` 566 passed / 16 skipped / 33 subtests；`realism_rules.py` 11 套全绿；`e2e_compare --label personal` 7 套 0 差异。

回滚：`git revert <本轮提交>`（只动 `contract.py::_scaffold`、`server.py::render`、新增一条测试）。

## 第十九轮（Chase 磁码行按底稿重写：DRE 段回来了）

底稿 `src/billgen/themes/chase/assets/chase_static_personal.pdf` 那一行（bl 130.3）是
**四段 0.24pt 微缩字**，不是我们原来以为的「路由号 + 账号三段」：

| 段 | x | 内容（个人） | 内容（企业） |
| --- | --- | --- | --- |
| 1 | 55.4 | `00462377`（账号前 8 位） | `30206041` |
| 2 | 88.0 | `DRE 703 219 28826 NNNNNNNNNNN` | `DRE 802 219 05926 NNNNNNNNNNN` |
| 3 | 210.0 | `  1 000000000 09`（期数 + 账号尾九位 + 期末月） | `  3 060411196 09` |
| 4 | 265.6 | `0000      AT 00`（on-us） | `0000` |

底稿整行**不含路由号**（`021000021` 在全文里根本不出现）。旧实现把第一段塞了 9 位路由、
第二段塞了 `{前4} {中4} {后4}`，把 DRE 段整段顶掉。

改法：`src/billgen/synth/adapters/chase.py` 加 `_DRE_PREFIX`，`_micr()` 返回
`acct`/`dre`/`cycle`/`extra`；两个 generate 的发射逻辑取 `acct`。
新增闸门 `micro_line_problems()`（缺 `DRE ` 或 on-us 就报），附反向单测。

## 第二十轮（usbank-business 的 `Account Summary` 为什么一直印不出来）

三步走才定位到根因，每一步都被前一步的「看起来正常」骗过一次：

1. 在 `_emit_summary()` 里补发标题 —— 出片没有。新开内容段（`append_new_text`，
   不吃旧段里可能没闭合的 `q … W n` 裁剪）也没有。
2. 阈值探测：`SUMMARY_TITLE_BL = 441.2 / 445.0` 没有、`448.0 / 470.0` 有 →
   不是画不出来，是**画完被擦掉**。
3. monkeypatch `Page.delete_units` 抓栈：擦除来自 `_materialize()` 里续页那一轮
   `delete_units(155, 712)`。`doc.fullcopy_page(0, -1)` 追加的续页**与主页共用同一条
   内容流 xref**，对续页整区擦除会改到主页；`for pg in pages: pg.commit()` 又是后写的
   覆盖前面的。

修法三处：
- `_detach_contents(doc, page)`：克隆后立刻把克隆页的内容流拆成独立对象，别再互相覆盖；
- `_stamp_summary_title(pages)`：标题挪到 `_materialize()` 之后单独补（与个人版同一行 441.25）；
- **真正的元凶**是 `src/billgen/branding.py` 里 `usbank-business` 的 `{legal}` 槽位：
  底稿那一行在 bl **428.15**，槽位却按个人版写成 437.15、矩形 `(18, 430, 300, 440)`——
  这个矩形压到了 `Account Summary`（bl 441.25）的包围盒，红章擦除连标题一起抹掉；
  又因为矩形里找不到原文，`_same_text()` 认不出「已经印过」，于是多印一行
  `U.S. Bank National Association`（这就是那行重复的来历）。按底稿实测改成
  `(18, 421, 400, 433.5)` / origin `(20.15, 428.15)` 后，标题回来、重复行消失。

基线重采：`tests/test_usbank_shared.py` 四条 `usbiz-*` GOLDENS；
`tools/render_regression.py refresh` 8 例。验证：`pytest` 567 passed /
16 skipped / 33 subtests；`realism_rules.py` 全绿（含 micro_line）；
`e2e_compare --label business` → usbank-business 页面尺寸/字体/DPI/墨量/三个区块标题全 OK
（页数 4 vs 2 属数据行数差异，INFO）。

回滚：`git revert <本轮提交>`（磁码 + usbank-business 两处可分开回滚）。

## 第二十一轮（U.S. Bank 披露页曾印出一整句鬼画符）

线上 `usbank-business-…-813ed296.pdf` 第 2 页肉眼可见：

```
… or write to us at 6 4 # BOL / B U JPB O F " 5 " UU P E JB U P O 1 o …
```

正文整句变成乱码。根因是 `src/billgen/branding.py` 里 `_THEME_FONTS` 还留着
`"usbank": "F6"`：`replace_literals()` 先把原文擦掉，再用 `cid_bytes()` 把替换串
按 CID 直写进 `/F6` 内容流。字形看着凑合，但**续页的资源表是按页记账的**，
2 字节 CID 与该页 ToUnicode 对不齐 → 文字层与肉眼都是鬼画符。

决策：把 usbank 从 `_THEME_FONTS` 删掉（只留 BoA 的 `Reg`，它的 `txt()` 自带宽度与
编码处理），U.S. Bank 回到 base-14。代价是字体表多一个未嵌入 Helvetica——
这个窟窿由 `FONT_PARITY_EXEMPT` 显式豁免并留注释，比「正文乱码」轻得多。

验证：第 2 页恢复成 `… or write to us at U.S. Bank National Association, P O Box 3528
Oshkosh, WI 54903-3528.`；`pytest` 567 passed / 16 skipped / 33 subtests；
`realism_rules.py` 全绿；`e2e_compare --label personal` 7 套 0 差异。

回滚：`git revert <本轮提交>`（只删一行 `_THEME_FONTS` 与 `_theme_module()` 的 usbank 分支）。

## 第二十二轮（换家银行出片：amex 标题被吃掉、chase 域名重影、真行名反而配假联系）

把「虚构行名塞进每一套版式」这条路走全（8 家都印一遍，不只是肉眼过的那几家），三处硬伤：

1. **amex 标题被啃掉前半**：页眉大标题是 `{name}® Rewards Checking Statement`（x 从 107.1 起），
   而 `PLACEMENTS["amex"]` 第二块矩形 `(107.1, 26.6, 172.7, 40.6)` 正好压在同一位置——
   换名后红章擦掉标题前几个字，再画一块 65×14 的小字标，出片读作
   `rust Bank® Rewards Checking Statement`。标题里已带全名，小字标直接删掉。
2. **chase 域名重影**：`cascade-trust-bank.comst-bank.com`。原因是主题的 subs 已经会把
   `Chase.com` 换成 data 里的 domain，`branding.TEXT_SLOTS["chase-personal"]` 里那条
   `{domain}` 又画了一遍，两个落点差 100pt → 叠字。删掉那条槽位。
   同时给 subs 加闸：`not b["domain"].lower().endswith("chase.com")` —— 真 Chase 时
   `real_traits` 派生出来的是 `jpmorgan-chase.com`，替换会把 donor 的 `Chase.com`
   换成 `jpmorgan-jpmorgan-chase.co`（域名规则与行名规则叠加）。
3. **真行名配假联系**：`real_traits("JPMorgan Chase")` 命中不了（表里只有
   `jpmorgan chase bank` / `chase`），退化成派生行址 `P O Box 51963 / Charlotte /
   1-800-555-0004` —— 一张真的 Chase 账单印着 555 号码和北卡信箱。
   加别名 `"jpmorgan chase"`，并把「本行出片不许出现 555 假号码 / 重影行名」
   做成闸门 `fake_contact_problems()` + 反向单测。

验证：`pytest` 568 passed / 16 skipped / 33 subtests；`realism_rules.py` 11 套全绿；
`e2e_compare --label personal` 有参考件的 5 套 0 差异、boa/wf 版式漂移 0 段。

回滚：`git revert <本轮提交>`（amex 词标 / chase 槽位 / 别名三处互不依赖）。
