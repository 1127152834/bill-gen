"""示例数据文件的指纹守卫。

这些 `data_*.json` / `replay_*.json` 既是模板的默认数据，也是黄金回归的输入。
编辑器服务在开发时会原地写它们（`/api/save`），一旦被写进真实样本或半成品，
渲染结果与历史基线就会一起漂移——2026-10-29 就因此红了一片用例。
所以这里钉死指纹：改动必须是有意的，且要同步重录基线。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

DIGESTS = {
    "src/billgen/themes/boa/editor/data_personal.json": "d4c86223016cbf40733f4f06a5a966084d13d9ee6f32e315f43394383d37d208",
    "src/billgen/themes/boa/editor/data_business.json": "9e9b9ea1f15dfdbe5ac3959db4782249937d97a638f6aaa1dd75d0878932e685",
    "src/billgen/themes/chase/editor_personal/data_personal.json": "2b1b134368a6ac5b1e83ee6ec857ef1e95d0f4b2503012b82456f002d48938ae",
    "src/billgen/themes/chase/editor_business/data_business.json": "b9f897fbbdc62b275dac6852b8d4645cf0a04f481a63bdbcde62a30cbc2cb271",
    "src/billgen/themes/chase/editor_personal/replay_personal.json": "d793479dc89445064cdca0cfe0177126cec7660e71b2967f34c9feea12d00603",
    "src/billgen/themes/chase/editor_business/replay_business.json": "8f2fe676373df2040c417d8b9448f7ab101e50826b0ad8a4bc6e936937ee520a",
    "src/billgen/themes/usbank/editor_us/data_us.json": "60287d7b70a661a5c708233afaffc77ee1e56b0f7ea8bb48af5231402a73c2e4",
    "src/billgen/themes/usbank/editor_biz/data_biz.json": "48fb6becd8b6d9a3dd28e5844abd1a9f8c254dfac95da938dcc990e6cdaac795",
    "src/billgen/themes/usbank/replay_us.json": "60287d7b70a661a5c708233afaffc77ee1e56b0f7ea8bb48af5231402a73c2e4",
    "src/billgen/themes/usbank/replay_biz.json": "48fb6becd8b6d9a3dd28e5844abd1a9f8c254dfac95da938dcc990e6cdaac795",
    "src/billgen/themes/wellsfargo/data_sample.json": "1da969cff1a108c276fa682125f35a96c0772a064e812270c794cc595907b637",
}


@pytest.mark.parametrize("relative,digest", sorted(DIGESTS.items()))
def test_sample_data_is_untouched(relative: str, digest: str):
    path = ROOT / relative
    assert path.is_file(), f"缺少示例数据：{relative}"
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    assert actual == digest, (
        f"{relative} 被改动了。\n"
        "如果这是有意的：同步更新 tests/fixtures/render_regression_v1.json 与 "
        "tests/test_usbank_shared.py 的基线，再改这里的指纹。\n"
        "如果是运行期写脏的：从 .artifacts/tpl-backup-*/themes/ 恢复。"
    )
