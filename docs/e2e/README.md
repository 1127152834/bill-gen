# 端到端对比：出片 vs 参考件

两轮实测数据（都是**真调 langgraph** 跑出来的账本，不是手搓 fixture）：

| 报告 | 账本来源 | 覆盖版式 |
| --- | --- | --- |
| [report-personal.md](report-personal.md) | `POST /api/generations` `subjectType: personal` | boa / wellsfargo / amex / mercury / capitalone / sofi / chase / usbank（个人） |
| [report-business.md](report-business.md) | `POST /api/generations` `subjectType: business` | boa / chase / usbank（企业） |

重跑：

```bash
curl -s -X POST localhost:8800/api/generations -H 'Authorization: Bearer devlocal' \
  -H 'content-type: application/json' \
  -d '{"subjectType":"personal","months":3,"accounts":{"checking":{"min":8,"max":14},"credit":{"min":6,"max":12}}}'
curl -s localhost:8800/api/generations/<id> -H 'Authorization: Bearer devlocal' -o /tmp/job.json
.venv/bin/python tools/e2e_compare.py --result /tmp/job.json --png --label personal
.venv/bin/python tools/e2e_compare.py --result /tmp/job.json --png --label business \
  --templates boa-business,chase-business,usbank-business
```

指标口径：页面尺寸/页数/字体交集/最低图效 DPI/首页墨量/区块标题/参考件身份泄漏/日期格式；
没有参考 PDF 的版式退回 layout json 比位置（容差 2.5pt），两者都没有就只做自检（amex），
浏览器端 pdf-lib 模板（alliant/ally）明确跳过。

## 闸门（每次改动都要跑）

```bash
.venv/bin/python tools/realism_rules.py            # 11 套 × 全部规则，退出码即断言
.venv/bin/python -m pytest -q                      # 568 passed / 16 skipped / 33 subtests
.venv/bin/python tools/gen_bank_forms_assets.py --check   # skill assets 与 default_data() 一致
```

容器内跑（镜像不含 `tools/`，先拷进去）：

```bash
docker exec bill-gen-v2-billgen-1 mkdir -p /app/tools
docker cp tools/realism_rules.py bill-gen-v2-billgen-1:/app/tools/
docker exec bill-gen-v2-billgen-1 python /app/tools/realism_rules.py
```

规则清单（`tools/realism_rules.py`）：

| 规则 | 查什么 |
| --- | --- |
| `COMMON_RULES` / `BANK_RULES` | 占位词、省略号、金额符号、逐家行文与列格式 |
| `date_order_problems` | 流水按日期升序（允许跨年回绕一次） |
| `summary_arithmetic_problems` | 摘要区 `期初 + 各分项 = 期末` |
| `page_number_problems` | `Page k of N` 只在账单页上、N = 带页码的页数 |
| `url_path_problems` | 参考件里带路径的网址必须原样出现（别把 `/disclosures` 剪掉） |
| `pdf_fingerprint_problems` | 字节里不许有 `billgen`/`PyMuPDF`，producer 必须是这家的真 producer |
| `font_parity_problems` | 出片字体表不许超出参考件（逐家显式豁免见 `FONT_PARITY_EXEMPT`） |
| `cross_bank_problems` | 换个行名印一遍：原行名残留、新行名没印出来，都算问题 |
| `micro_line_problems` | Chase 磁码行必须有 `DRE …` 与 on-us 段 |
| `product_mismatch_problems` | 别把同一家银行**另一个产品**的号码/路径印上来（amex 存款账单不许出现 1-800-528-4800 这类 Card Member 线） | 11 套 |
| `fake_contact_problems` | 不许 555 假号码；不许替换叠加成 `jpmorgan-jpmorgan-chase.co` |

## 已知限制（不是 bug，别当回归）

1. **amex 没有参考件（真实存款账单样板）**：仓库里只有 `logo.png` + 三份 BentonSansAE 字体，
   主题是手写的，所以只能自检 + 逐项人眼过，没有逐像素基线。
   能抓到的公开 amex PDF 都是**信用卡**账单（如 Hilton Honors Business Card，12 页扫描件，
   版式是 `New Balance / Minimum Payment Due / Payment Due Date`），与这份 Rewards Checking
   存款账单不是同一产品，拿来当基线只会把信用卡版式抄进来。
   接触信息改按 Amex 自己的《Rewards Checking 账户条款》核对过：
   `1-877-221-2639`（US Toll Free）/ `1-801-945-2639`（International collect）/
   `PO Box 31492, Salt Lake City, UT 84131-0300` / `americanexpress.com/rewardschecking`；
   信用卡侧的 `1-800-528-4800`、`P O Box 981540, El Paso, TX` 留在
   `ledger.real_traits("american express card")` 里，不印到存款账单上。
2. **BoA 换行名时联系块走 base-14**：BoA 主题字体是 Connections 子集（CFF 自定义编码），
   把子集字形塞进 `insert_text(fontfile=…)` 会打乱编码；同家银行时 `_same_text()` 会整块跳过，
   所以只有「用 BoA 版式印别家银行」这一种情况会多出未嵌入的 Helvetica。
3. **U.S. Bank 的 Helvetica 豁免**：`FONT_PARITY_EXEMPT` 显式放过 `Helvetica`。
   曾试过用 `cid_bytes()` 直写主题字体，字形对但**文字层是乱码**（续页资源表按页记账，
   2 字节 CID 与该页 ToUnicode 对不齐），比多一个字体严重得多，已回退。
4. **信用卡账户不出片**：11 套版式全是存款账户；`render.ts` 会把跳过的账户写进
   `result.warnings`（早先是静默跳过）。
5. **离线 fixture 限制**：`subjectType=business` 需要同时给 `checking` + `credit`
   （lifeModel fixture 把订阅挂在 credit 上）；`months` 只接受 1/3/6；
   企业**整单**在 `LLM_FAKE=1` 下的人格层能过，research/extract 仍是个人口径。
6. **页数不等于参考件**：参考件是固定行数的样板，我们按真实账本行数分页
   （`usbank-business` 4 页 vs 底稿 2 页属数据行数差异）。续页横幅、客户块、
   `(CONTINUED)`、`Page k of N` 都已按底稿范式复刻。
7. **`alliant` / `ally`**：`kind="static-web"`，浏览器端 pdf-lib 渲染，Python 侧无出片路径。

8. **Chase 续页有 1pt 隐形标记**：`*start*dre portrait disclosure message area` /
   `*end*…`，出自参考件本身（`themes/chase/layout_personal.json` 的 `spans[58]/[59]`），
   肉眼不可见。删它要连 layout json 一起删，否则 layout 比对会报缺 span。

## 目标完成度自评（收口）

| 目标项 | 证据 |
| --- | --- |
| ① langgraph 走通真实生成 | 线上 `POST /api/generations` 个人 job `dc2006c4`（checking 42 笔 + credit 36 笔）、企业 job `5e5543aa`（business_checking + credit），均 `completed` |
| ② 逐套出片 + 与参考件逐项对比 | [report-personal.md](report-personal.md)（8 套：5 套有参考 PDF 差异 0 项、boa/wf 版式漂移 0 段、amex 无参考件只自检）、[report-business.md](report-business.md)（boa 漂移 0 段、chase/usbank 差异 0 项）+ `img/` 8 张并排图 |
| ③ 差异逐条修复并复验 | 24 轮记录见 [../notes/e2e-round1.md](../notes/e2e-round1.md)，每轮都带复验数据与回滚命令 |
| ④ 每步 commit + push | 45 个提交全部推到 `origin/master` |
| ⑤ 全程笔记 | `docs/notes/e2e-round1.md`（24 轮 + 回滚）、本文件（闸门清单 + 已知限制） |

当前闸门状态：`pytest` 569 passed / 16 skipped / 33 subtests；`realism_rules.py` 全部规则通过；
`gen_bank_forms_assets.py --check` 一致；两份对比报告 0 差异、0 段漂移。
