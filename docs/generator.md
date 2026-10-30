# 账单 JSON 生成器（替代账单猎手）

工作台：`http://127.0.0.1:8800/generate/`。给它一份档案（人 / 企业）和一句「哪家银行、哪几个月」，
它用真模型 + Tavily 调研编出一套可信的流水，最后交付**每月一份该银行模板的 JSON**，你自己导进模板印账单。

决策与取舍见 [`docs/DECISIONS.md`](DECISIONS.md)；交付形态与三层校验见 [`docs/month-json.md`](month-json.md)。

## 一条任务跑过什么

```
normalize → [planResearch → research(Tavily)] → lifeModel → schedule
        → descriptors → validate → audit(↺ lifeModel) → finalize
        → month_artifacts → check_delivered → merge_entry → postpass
```

方括号那段只在 `BILLGEN_SCHEDULER=model`（旧链路）里跑。默认的 `sampler` 路径**不查 Tavily**：
品牌、门店号、价格档位都来自 `src/billgen/gen/sampler.py` 的内置分布表，检索只会多花时间与配额。

- **AI 只负责语义**：这个人做什么工作、住哪、去哪买菜、账单叫什么名字。
- **钱一分不交给 AI 算**：期初、逐行余额、期末、合计、跨月衔接全部由 `monthpack` 与 `build_ledger()` 重算，
  `期初 + Σ金额 = 期末` 与「上期末 = 下期初」是硬卡，不平就报 `validation-report.json`。
- **audit 只写 warnings**：模型自审发现的疑点不会拦截交付，只会写进 `warnings` 让你自己看。

## 流水怎么来：模型定形态，代码抽分布

逼真度是**分布**问题，不是文案问题：品类占比、两次消费的间隔、金额分布、星期偏好、
月度周期（房租 1 号、工资每两周一次、订阅固定日扣）。模型对分布没有稳定记忆，
所以默认链路里模型只回答「这个人怎么生活」，具体每一笔由 `sampler.py` 从分布里抽：

| 决定项 | 谁定 | 依据 |
| --- | --- | --- |
| 笔数 | 采样器 | 泊松式抽样（`CategoryModel.per_month` 为 λ），再按版式行数区间等比缩放 |
| 金额 | 采样器 | 对数正态（杂货中位 $86、餐饮 $28、咖啡 $7.6、加油 $42），带上下限 |
| 日期 | 采样器 | 星期偏好 × 周末系数 × 发薪日邻近 1.6 倍加权 |
| 月度周期 | 采样器 | 房租 1 号、电 14、气 16、水 9、网 7、话费 12、保险 5 |
| 同族账单每月一笔 | 采样器（构造保证） | 不靠事后去重 |
| 期初余额 | 采样器 | 按期初往下走，任何时点不透支，且不低于 $250 |

`BILLGEN_SCHEDULER=model` 可切回「模型逐行编 + postpass 事后修」的老链路，仅用于对比与回归。

### 分布验收口径

`tools/distribution_report.py` 把两份月账单 JSON 的分布并排列出来（不是看文案，是看形状）：

```bash
.venv/bin/python tools/distribution_report.py     .data/jobs/<旧模型单>/mercury-2026-09.json     .data/jobs/<采样单>/mercury-2026-09.json --labels model,sampler
```

同一份档案（HENG LI，Temple City CA，月入 9000–14000）的两份 2026-09 交付件对比：

| 指标 | model（旧链路） | sampler（默认） |
| --- | --- | --- |
| 流水行数 | 24 | 31 |
| 支出笔数 | 19 | 29 |
| 支出合计 | 2,308.74 | 4,664.11 |
| 收入合计 | 3,622.20 | 11,699.02（落进档案档位） |
| 周末消费占比 | 64% | 50% |
| 同店同额复读率 | 0% | 0% |
| 占比 · grocery / dining / fuel | 42% / 32% / 11% | 21% / 28% / 14% |
| 间隔 CV · grocery | 1.46 | 0.65 |
| 每月固定账单 | 靠 postpass 去重 | 构造保证各一笔 |

读法：

- **同店同额复读率**：两份都是 0%；旧链路偶尔出现同店同额连刷（如两次 `$27.00` 星巴克）。
- **间隔 CV**：旧链路杂货 1.46（忽疏忽密），采样单 0.4–0.9，接近泊松的 1.0 但不呆板。
- **固定账单**：采样单每月每族各一笔；旧链路要靠 `postpass` 去重才不出两份电费。
- **收入**：旧链路经常与档案档位脱节，采样单按 `monthly_income_hint` 反推单笔金额。

跑这条对比要固定档案与月份，否则差异会混入「人不一样」的噪声。

## 三层校验

| 层 | 拦什么 | 在哪 |
| --- | --- | --- |
| 结构 | 缺字段、类型不对、清单元素形状不对（以模板默认值为标尺） | `monthpack._structure_issues` |
| 单月 | 占位词（SAMPLE/EXAMPLE/…）、流水合计 ≠ 期末−期初、行数为 0、日期越界 | `monthpack._content_issues` + `json_total_issues` |
| 跨月 | 断月、上期末 ≠ 下期初、模板写法混用 | `monthpack.validate_batch` |

失败的那一个月照样交付（`ok:false` + 具体报错），整批标「部分失败」，不会把坏文件当成功。

## 交付前收尾（`src/billgen/gen/postpass.py`）

模型再怎么提示也会犯的几类错，交付前做确定性修正（每项计数写进 `job-summary.json` 的 `postpass`）：

| 项 | 例 |
| --- | --- |
| 跨区公用事业 | Temple City 印了 `LADWP`/`PG&E` → `SOUTHERN CALIFORNIA EDISON` + `SO CAL GAS` |
| 同月重复账单 | 两份电费只留金额最大那张 |
| 越区连锁 | 加州印了 `WHATABURGER`/`QUIKTRIP`/`KROGER` → 换本地品牌并保留门店号 |
| 线上商户带城市 | `NETFLIX.COM AUSTIN TX` → `NETFLIX.COM`（双词城市也不会剥错） |
| 金额量级 | 加油 `$4.56` → 拉回 `$24–95`，改完重算期末 |
| 收支档 | 档案写 `rent_hint 2600-3200`，$275 的房租被拉回档位中点；收入按档位中点放大 |

## 命令行 / 接口

```bash
# 起服务（工作台 + 生成 API）
python -m billgen serve --port 8800

# 提交一单：3 个月 = 3 份 JSON
curl -s -H 'Authorization: Bearer devlocal' -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:8800/api/v1/gen/jobs \
  -d '{"profile_id":"<id>","template":"mercury","months":["2026-08","2026-09","2026-10"]}'

# 看进度 / 取产物
curl -s -H 'Authorization: Bearer devlocal' http://127.0.0.1:8800/api/v1/gen/jobs/<job>
curl -s -H 'Authorization: Bearer devlocal' -o out.zip http://127.0.0.1:8800/api/v1/gen/jobs/<job>/package
```

产物落 `data/jobs/<id>/`：`<template>-YYYY-MM.json`、`validation-report.json`、`manifest.json`、`job-summary.json`。

## 档案库

`data/billgen/profiles.json`（可用 `BILLGEN_PROFILES` 覆盖）。个人要 `name` + `address{street,city,state,zip}`，
企业要 `legalName` + `entityType(LLC|S-Corp|Sole Prop)` + `address`。

账户事实（账号、路由号、开户日）首次保存时按档案 id 派生并写回，之后**只补不改**——手改过的值永远保留。
可选 `monthly_income_hint` / `rent_hint`（`"9000-14000"`、`"$2,600-$3,200"` 都认），填了就当硬约束用。

批量导入：`POST /api/v1/profiles/import`，`{"text": "...", "format": "json"|"csv"}`，逐行报错（行号从 1 起）。

## 预算信封与节日/季节（`gen/envelope.py` + `gen/seasonal.py`）

流水「真不真」首先是分布问题，不是措辞问题。所以每月先按收入档算一张预算信封，再按美国日历缩放：

1. **信封**：`CATEGORY_SHARES` 把净收入的固定比例分给 rent / groceries / dining / fuel / utilities /
   phone / insurance / subscription / shopping / health；`col_tier` 再按地区调一档。填了 `rent_hint`
   就直接用，房租不随季节变。
2. **季节**：`ENVELOPE_FACTORS` 按月份缩放（7–8 月水电气 ×1.35、11 月购物 ×1.9、12 月 ×2.0、
   11 月食材 ×1.35…）。每月信封与「本月节日」一起写进 lifeModel 提示词，模型据此安排叙事。
3. **节日日历**：`us_holidays()`（感恩节 = 11 月第 4 个周四、复活节用 Computus、母亲节/父亲节按第几个周日…）
   + `retail_events()`（超级碗、报税日、返校、Prime Day、黑五、网一、圣诞礼品）。
4. **交付前补齐**（`postpass.top_up_to_envelope`）：低于信封下限的类目先抬已有行（单笔受
   `CATEGORY_RANGES` 约束），抬到顶还不够就补几笔同类消费；公用事业缺一家时把该州另一家补上
   ——真账单本来就是电、气各一张。`job-summary.json` 里 `envelope` 记录偏差、`topped_up` 记录补齐动作。
5. **节日消费**（`seasonal.inject_event_rows`）：某类目还有余额时补 1 笔当月事件消费
   （感恩节食材、黑五 TARGET/BEST BUY、12 月礼品），金额不越信封上限。

改完金额一律 `_rescale_amounts()` + `sort_rows()`，保证「期初 + 流水 = 期末」且日期不倒序。

## 测试

```bash
PYTHONPATH=.:src .venv/bin/pytest tests/test_gen_pipeline.py tests/test_gen_postpass.py \
  tests/test_gen_runner.py tests/test_gen_pages.py tests/test_monthpack.py -q
```

真模型跑一单大约 3–5 分钟（`llmCalls 7`、`tavilyQueries 12–14`、`tokens ≈ 22k prompt / 7k completion`）。
不想花额度时用 `LLM_FAKE=1`（离线 fixture，只用于测试）。
