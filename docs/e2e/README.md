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
