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
