"""SoFi 的流水是从新到旧印的，余额列也要按这个方向回推。

参考件 `src/billgen/themes/sofi/SoFi-Statement-SAMPLE.pdf` 第 1 页的日期顺序是
Sep 30 → Sep 3（其余银行都是旧到新）。这条写死成测试，免得账本层统一按升序以后
又把它改回去——顺带把「余额列必须跟方向一致」一起钉住：错方向的余额列看起来只是
数字不对，实际上每一行的期初期末都错位了。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from billgen.synth.bridge import from_game_result, render

FIXTURE = Path(__file__).parent / "fixtures" / "synth_result.json"

ROW = re.compile(r"^(\d{2})/(\d{2})/(\d{4})\s+(.+?)\s+(-?\$[\d,]+\.\d\d)\s+(\$[\d,]+\.\d\d)$")


def _cents(text: str) -> int:
    negative = text.startswith("-")
    value = int(round(float(text.replace("-$", "").replace("$", "").replace(",", "")) * 100))
    return -value if negative else value


def _lines(pdf: bytes) -> list[str]:
    """按基线把 span 拼成整行（PDF 里日期、描述、金额、余额是各自的 span）。"""
    import fitz

    out: list[str] = []
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        for page in doc:
            buckets: dict[float, list[tuple[float, str]]] = {}
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        text = span["text"].strip()
                        if text:
                            buckets.setdefault(round(span["origin"][1], 1), []).append((span["bbox"][0], text))
            for base in sorted(buckets):
                out.append(" ".join(text for _, text in sorted(buckets[base])))
    return out


def _rows() -> list[tuple[tuple[int, int, int], int, int]]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    ledger = from_game_result(payload, watermark=False, template="sofi")
    rows: list[tuple[tuple[int, int, int], int, int]] = []
    for line in _lines(render("sofi", ledger).pdf):
        hit = ROW.match(line.strip())
        if hit:
            rows.append(((int(hit.group(3)), int(hit.group(1)), int(hit.group(2))),
                         _cents(hit.group(5)), _cents(hit.group(6))))
    return rows


def test_sofi_prints_newest_first_and_balances_chain():
    rows = _rows()
    assert len(rows) >= 5, f"没抓到足够流水行：{rows}"
    dates = [r[0] for r in rows]
    assert dates == sorted(dates, reverse=True), f"SoFi 应按新→旧印：{dates}"
    for (_, amount, balance), (_, _, next_balance) in zip(rows, rows[1:]):
        assert balance - amount == next_balance, f"余额列方向错位：{balance} - {amount} != {next_balance}"
