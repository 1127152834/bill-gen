# 本地知识库（bank statements）

目的：把「哪家银行、哪份账单、要什么数据、按什么规则写」全部落到本地，**离线可查、可校验、可刷新**。不依赖上网，也不靠记忆。

```
knowledge/
├── sources.json          登记簿：每条来源的 id / 类型 / 取数方式 / 抓取时间 / sha256 / 状态
├── raw/<id>.txt          抓下来的正文（PDF 抽文本、HTML 去标签）+ 元数据头
├── banks/<theme>.json    每家银行的数据字典：身份 / 费用 / 账单规则 / 缺口（带原文引用）
├── regulations.md        法规与卡组织/Nacha 规则 → 约束我们哪条规则（含当前违反项）
└── README.md             本文件
```

## 一、三种可复现的取数方式

| 方式 | 适用 | 例子 |
| --- | --- | --- |
| `url` + 直接 GET | 银行产品页、PDF 收费表、eCFR API | `chase-personal-checking`、`boa-personal-fees`、`reg-dd-1030` |
| `mode: "fdic"` + `cert` | 银行法定名称 / 总部地址 / 官网（**抬头与页脚的一手来源**） | `fdic-chase` → `JPMorgan Chase Bank, National Association` |
| `mode: "fdic"` + `search` | 不确定 CERT 时先探 | 已全部改成按 CERT 精确命中，避免模糊搜索命中别家银行 |

## 二、怎么用

```bash
# 刷新（24 小时内不重复下载；--force 强制）
python tools/fetch_knowledge.py
python tools/fetch_knowledge.py --only chase,boa --force

# 看现状 / 只看有没有变
python tools/fetch_knowledge.py --report
python tools/fetch_knowledge.py --check

# 由原文生成每家银行的数据字典（会校验引用片段真的出现在原文里）
python tools/build_bank_kb.py
python tools/build_bank_kb.py --check     # CI 用：只校验不写文件
```

`knowledge/banks/<theme>.json` 的结构：

```jsonc
{
  "theme": "chase",
  "facts": [
    {"field": "fees.monthly_service_fee_personal", "value": "$15（可豁免）",
     "source": "chase-personal-checking", "quote": "$15 or $0 Monthly Service Fee", "confidence": "high"}
  ],
  "sources": ["chase-personal-checking", "chase-business-checking", "fdic-chase"],
  "gaps": ["..."],          // 诚实度计：没核对到的东西写在这里
  "problems": []            // 引用对不上原文时才会非空（此时 build 退出码 1）
}
```

**纪律**：每条事实必须带 `source` + 原文 `quote`。`quote` 不在原文里就构建失败——这一条拦住「编造出处」，也是这套东西唯一值得信的地方。`confidence` 只有 `high` 才能进契约，`low` 的只作提示。

## 三、现在覆盖到什么程度

- **来源 34 条**，落地 31 条（`knowledge/raw/` 共 ~1.1MB 文本）。10 家银行的身份全部来自 FDIC（法定名称、总部、官网），个人/企业双主体的 Mercury 走 Column N.A. 与 Choice Financial Group 两条。
- **银行事实 57 条**（自动校验通过），其中 `high` 置信度的直接来自银行产品页或协议原文，例如 Chase 月费 $15 与豁免条件、WF 透支费 $35/笔、U.S. Bank 月费 $12、SoFi 透支覆盖上限 $50、Mercury 客服地址与电话。
- **法规 3 部全文**（Reg DD / Reg E / Reg CC，eCFR 官方 XML）+ Nacha 规则变更解读 + Check 21 + 卡组织描述符规范 + USDA 食品成本。
- **不落地的东西**：整份受版权保护的银行 PDF 只留引用片段；账单样例仍用仓库里已有的参考件（`src/billgen/themes/*/assets/*.pdf`），不往外传。

## 四、抓不到 / 待补的（诚实清单）

| 来源 | 状态 | 影响 | 备选 |
| --- | --- | --- | --- |
| BoA 商户描述符 PDF | 504 | 卡行描述符规则改用 Cybersource 权威页 | 已用 `cybersource-descriptors` 顶替 |
| CFPB 银行账户工具页 | 403 | 监管口径改用 eCFR 原文 | `reg-dd-1030` / `cfpb-tisa` 未落地 |
| BLS 消费支出表 | 403（IP 级拦） | 预算信封的类目占比暂用已有 `envelope.py` 常量 | 人工抄 2024 CE 表进 `knowledge/banks/_stats.json`（待做） |
| Capital One 披露页 | 抓到但 JS 渲染，无可引用事实 | C1 契约只能靠本地参考 PDF | 需真实账单核对 |
| BoA 收费表金额配对 | PDF 表格串行，未逐项配对 | BoA 费用行金额仍是 `low` | 需人工按账户类型抄一遍 |
| WF 收费与信息表正文 | 只有 Addenda | WF 月费豁免条件未核实 | 重试 `fetch-pdf` 其它 documentName |

## 五、下一步（按价值排序）

1. **把 Reg DD § 1030.6 的三项披露、Nacha 的 `PAYROLL`/`PURCHASE` 规则接进契约**（见 `regulations.md` 汇总）——这是真实账单与我们最大的差距，且不需要 AI。
2. **BLS CE 表人工落一份**（`_stats.json`），让预算信封的类目占比从"我拍的"变成"有出处的"。
3. **BoA / Capital One 拿真实账单核对**，把 `low` 置信度的事实升级或删掉。
4. 每季度跑一次 `fetch_knowledge.py --check`，变了的来源人工过一遍（银行改收费表很常见）。

## 六、许可与合规

`knowledge/raw/` 里是**引用片段**（用于核对与检索），不是整份文件的分发。对外交付时只带 `banks/*.json` 与 `regulations.md` 的结论与出处链接，不带原文全文。
