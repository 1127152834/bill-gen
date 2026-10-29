# 端到端对比：出片 vs 参考件

- 账本期数：2 份
- 水印：开

## wellsfargo

无参考 PDF，改用 layout json 做结构比对（34 段文字）

- 版式漂移：1 段（容差 2.5pt）
  - '-' 偏移 x+0.0 基线+11.2
- 页数：6；墨量：[7.7, 10.1, 6.3, 6.0, 9.6, 10.1]；字体：Helvetica, Helvetica-Bold, MyriadPro-Black, MyriadPro-Bold, MyriadPro-BoldIt, MyriadPro-It, MyriadPro-Regular, SymbolMT,

## usbank-personal

参考件：`src/billgen/themes/usbank/assets/us_static_personal.pdf`（4p，612.0×792.0）

| 方面 | 出片 | 参考件 | 判定 |
| --- | --- | --- | --- |
| 页面尺寸 | (612.0, 792.0) | (612.0, 792.0) | OK |
| 页数 | 4 | 4 | OK |
| 字体（共有） | Arial-BoldItalicMT, Arial-BoldMT, Arial-ItalicMT, ArialMT, C0000365_T1001140_0,  | 参考共 9 种 | OK |
| 字体（仅出片） | Helvetica-Bold | — | INFO |
| 最低图效 DPI | 240.4 | 240.4 | OK |
| 第 1 页墨量 % | 15.4 | 13.3 | OK |
| 区块标题 Uni-Statement | 有 | 有 | OK |
| 区块标题 SAFE DEBIT ACCOUNT | 有 | 有 | OK |
| 区块标题 Deposits / Credits | 有 | 有 | OK |
| 区块标题 Other Withdrawals | 有 | 有 | OK |
| 参考件身份/占位泄漏 | 无 | — | OK |
| 日期格式 | Mon D, Mon DD, YYYY, Month D, YYYY | MM/DD, Mon D, Month D, YYYY | OK |

差异：0 项

