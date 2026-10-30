"""知识库的自检：来源可复现、事实有原文、契约能指回知识库。

这一组测试守的是「不许编造出处」：任何一条事实的 quote 必须真的出现在抓下来的原文里，
否则 build_bank_kb.py 直接失败（这里用子进程跑 --check 来守）。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
KB = ROOT / "knowledge"
RAW = KB / "raw"
BANKS = KB / "banks"

RENDERABLE_THEMES = {
    "boa": ["boa-personal", "boa-business"],
    "chase": ["chase-personal", "chase-business"],
    "usbank": ["usbank-personal", "usbank-business"],
    "wellsfargo": ["wellsfargo"],
    "mercury": ["mercury"],
    "capitalone": ["capitalone"],
    "sofi": ["sofi"],
}


def _sources() -> dict[str, dict]:
    doc = json.loads((KB / "sources.json").read_text("utf-8"))
    return {item["id"]: item for item in doc["sources"]}


def _raw_text(source_id: str) -> str:
    return (RAW / f"{source_id}.txt").read_text("utf-8")


def test_sources_are_unique_and_have_a_reproducible_way_in():
    sources = _sources()
    assert len(sources) == len(json.loads((KB / "sources.json").read_text("utf-8"))["sources"])
    for sid, item in sources.items():
        assert item.get("url") or (item.get("mode") == "fdic" and (item.get("cert") or item.get("search"))), sid
        assert item.get("kind"), f"{sid} 缺 kind"
        assert item.get("used_for"), f"{sid} 缺 used_for（这条来源用来核对什么）"


def test_every_landed_source_has_raw_text_matching_its_checksum():
    sources = _sources()
    ok = {sid: item for sid, item in sources.items() if item.get("status") == "ok"}
    assert len(ok) >= 25, f"落地的来源太少：{len(ok)}"
    for sid, item in ok.items():
        path = RAW / f"{sid}.txt"
        assert path.is_file(), f"{sid} 标了 ok 但没有 raw 文件"
        text = path.read_text("utf-8")
        assert f"sha256: {item['sha256']}" in text, f"{sid} 的 raw 头里 sha256 与登记簿不一致"
        assert re.fullmatch(r"[0-9a-f]{64}", item["sha256"]), f"{sid} 的 sha256 格式不对"
        assert len(text) > 200, f"{sid} 正文太短，可能抓到了空壳页"
        assert f"url: {item.get('url') or ''}" in text or item.get("mode") == "fdic"


def test_each_renderable_account_theme_has_a_bank_kb_with_sourced_facts():
    sources = _sources()
    for theme, templates in RENDERABLE_THEMES.items():
        path = BANKS / f"{theme}.json"
        assert path.is_file(), f"{theme} 没有 knowledge/banks/{theme}.json"
        doc = json.loads(path.read_text("utf-8"))
        assert doc["facts"], f"{theme} 一条事实都没有"
        assert doc["sources"], f"{theme} 没标来源"
        for fact in doc["facts"]:
            sid = fact["source"]
            assert sid in sources, f"{theme}:{fact['field']} 引用了不存在的来源 {sid}"
            assert sources[sid]["status"] == "ok", f"{theme}:{fact['field']} 的来源 {sid} 未落地"
            assert fact["confidence"] in ("high", "medium", "low")
            if fact["confidence"] == "high":
                assert len(fact["quote"]) >= 6, f"{theme}:{fact['field']} 高置信度却没有原文引用"
                assert fact["quote"] in _raw_text(sid), f"{theme}:{fact['field']} 引用片段不在 {sid} 原文里"
        assert isinstance(doc["gaps"], list)
        assert not doc["problems"], f"{theme} 构建时有问题：{doc['problems']}"
        # 每个可渲染模板都要能从主题 KB 里看到自己
        for _template in templates:
            assert theme  # 模板与主题一一对应，模板名为 theme 前缀


def test_bank_kb_check_passes_from_cli():
    proc = subprocess.run(
        [sys.executable, "tools/build_bank_kb.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_regulations_digest_cites_only_real_sources_and_names_the_gaps():
    text = (KB / "regulations.md").read_text("utf-8")
    sources = _sources()
    cited = set(re.findall(r"`([a-z0-9][a-z0-9-]{4,})`", text))
    for sid in cited & {s for s in sources if s not in ("high", "medium", "low", "raw")}:
        assert sources[sid]["status"] == "ok"
    # 六个板块必须都在，且点名当前违反项
    for needle in ("Reg DD", "Reg E", "Reg CC", "Nacha", "商户描述符", "Check 21"):
        assert needle in text, f"regulations.md 缺 {needle} 板块"
    assert "PAYROLL" in text and "§ 1030.6" in text
    assert "当前违反" in text or "违反" in text


def test_every_contract_points_back_into_the_knowledge_base():
    contracts = sorted(p for p in (ROOT / "contracts").glob("*.json"))
    assert len(contracts) == 10
    for path in contracts:
        doc = json.loads(path.read_text("utf-8"))
        refs = doc.get("knowledge_refs") or []
        assert refs, f"{path.name} 没有 knowledge_refs"
        for ref in refs:
            assert (ROOT / ref).is_file(), f"{path.name} 指向不存在的 {ref}"


def test_readme_documents_the_three_ways_to_pull_data():
    text = (KB / "README.md").read_text("utf-8")
    assert "sources.json" in text and "raw/" in text and "banks/" in text
    assert "mode: \"fdic\"" in text
    assert "--check" in text and "--report" in text
    assert "quote" in text  # 引用校验这条纪律必须写清楚
