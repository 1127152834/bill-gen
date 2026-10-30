# Mercury 账户验证信（verification letter）模板

给客户出「账户验证信 / To Whom It May Concern」那封带 Mercury 抬头、Column N.A. 信息的信。
版式按参考件 `mercury-bank-letter-7658.pdf` 量出来的坐标 1:1 复刻：612×792、页眉徽标、
三条分隔细线、三节表格、页脚说明、手写签名。

## 怎么用

```bash
# 1) 看默认数据（拿去当模板改）
.venv/bin/python -m billgen.themes.mercury.letter --show-defaults > letter.json

# 2) 改 letter.json 里的字段，出 PDF（顺手导一张 PNG 预览）
.venv/bin/python -m billgen.themes.mercury.letter --data letter.json \
    --out letter.pdf --png letter.png

# 想标成样张就加 --watermark
```

代码里调：

```python
from billgen.themes.mercury.letter import build, save

pdf = build()                                   # 默认数据
pdf = build({"company": {"name": "Acme LLC"}})  # 只改一个字段，其余走默认
save("letter.pdf", {"date": "2026-11-05"})
```

## 能改什么

| 字段 | 说明 | 版式上限 |
| --- | --- | --- |
| `date` | 出信日期。`2026-11-05` 会自动排成 `November 5, 2026` | 1 行 |
| `salutation` | 称呼，默认 `To Whom It May Concern:` | 1 行 |
| `company.name` | 客户公司名（正文里**加粗**，宽度按真实字形算，会长到下一行） | 正文最多 3 行 |
| `company.signatory` | 账户签署人 | 1 行 |
| `company.address` | 公司地址，字符串数组 | 2 行 |
| `account.opened` | 开户日期 | 1 行 |
| `account.routing` | 路由号（`121,145,433` 这种带逗号的会自动洗成裸数字） | 1 行 |
| `account.number` | 账号 | 1 行 |
| `bank.name` | 银行名，默认 `Column N.A.` | 1 行 |
| `bank.address` | 银行地址，字符串数组 | 2 行 |
| `signer.name` / `signer.title` | 签署人姓名 / 头衔 | 1 行 |
| `signer.signature` | `true` 保留参考件的手写签名；`false` 用白块盖掉 | — |
| `footer` | 页脚那行 Column 说明 | 1 行 |
| `watermark` | 是否盖 SAMPLE 水印（也可用 `--watermark`） | — |

放不下时的行为：先按字形宽度自动缩字号（下限 7pt），再放不下就**报错**
（`ValueError`），不会硬挤出边界把版式压坏。

## 在网页上改（推荐）

不想手写 JSON 就用这个：起一个本地小服务，左边填字段、右边实时看 PDF。

```bash
.venv/bin/python tools/letter_studio.py --open      # 自动开浏览器，默认 http://127.0.0.1:8770
```

页面能做什么：

- 六个分组（信件 / 客户 / 账户 / 银行 / 签署 / 页脚）覆盖全部字段，改完 0.2 秒自动重出片；
- **下载 PDF**、**下载 PNG**（PNG 走 `/api/png`，144dpi）；
- **导出 JSON** 存成 `letter.json`，和 `--data letter.json` 那条命令行路径完全互通；
- **导入 JSON** 回填、**恢复默认**一键还原；
- 字段值存在浏览器 `localStorage`，刷新/关掉再开还在；
- 行数或长度超限时，红条里直接说人话，例如
  `公司地址给了 3 行，版式只放得下 2 行（超了会压到分隔线）`。

接口就四个，想接自己的前端也行：`GET /`（编辑器页面）、`GET /api/defaults`（默认数据）、
`POST /api/render`（JSON → `application/pdf`）、`POST /api/png`（JSON → `image/png`）。

## 为什么是「空白信纸 + 重绘文字」

参考件的文本层里留着别人的公司名、账号、签署人姓名。只盖白块不擦除的话，
`search` / 复制 / 文本抽取 还能捞到这些值——那比肉眼可见更严重。
所以底稿是用 `tools/make_mercury_letter_base.py` 生成的：

```bash
.venv/bin/python tools/make_mercury_letter_base.py \
    --src ~/Downloads/mercury-bank-letter-7658.pdf \
    --out src/billgen/themes/mercury/assets/letter/verification_letter_blank.pdf
```

它把可变内容（日期、正文、各行的值、签署人姓名与头衔、`To Whom It May Concern:`、
`Sincerely,`）从内容流里真正删掉，只留版式家具（顶部浅蓝条、徽标、三条细线、
行标签、蓝色小节标题、页脚）。默认**保留**手写签名（默认数据就是那位签署人）；
要用干净底稿就加 `--strip-signature`。

## 字体

`src/billgen/themes/mercury/assets/letter/` 下放了从 `~/Documents/字体/` 取的
`ArcadiaText-360.ttf`（正文）与 `ArcadiaText-480.ttf`（强调：公司名、签署人姓名）。
与参考件的嵌入子集相比，同字号下整行宽度约宽 0.9%，正文折行位置可能比参考件早一个词——
其余每个字段的基线/起点与参考件完全一致（见 `tests/test_mercury_letter.py` 的坐标表）。

## 测试

```bash
.venv/bin/python -m pytest tests/test_mercury_letter.py tests/test_letter_studio.py -q
```

`tests/test_mercury_letter.py` 钉版式坐标与字段替换，`tests/test_letter_studio.py` 起真服务打真请求
（页面 / 出片 / PNG / 超限 400 / 404）。

覆盖：坐标逐条对齐参考件、页面尺寸与三条细线、字段可改且旧值不残留、
账号带千分位会被洗净、长公司名不越右边界、超行数/超长值报错、
底稿里不留参考件身份信息、`--png` 落盘。
