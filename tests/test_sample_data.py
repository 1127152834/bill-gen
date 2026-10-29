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
    "src/billgen/themes/boa/editor/data_personal.json": "a759cd04fa0bdb3f5b9ffbfd5eacf4666ecac6ed16511a5f0e7c548823406acd",
    "src/billgen/themes/boa/editor/data_business.json": "c87af1459f6778689678527c097c8966349d50f41558e6c9d5aae8aae3930f26",
    "src/billgen/themes/chase/editor_personal/data_personal.json": "65f78334b5f8bd01dc54ecf11837568cb8513a43d78a0921d1fae34dbc6d9859",
    "src/billgen/themes/chase/editor_business/data_business.json": "09bfca27fd0fdb55951633d1da8e020add42935a2d6ef3d45711b3c72e385210",
    "src/billgen/themes/chase/editor_personal/replay_personal.json": "65f78334b5f8bd01dc54ecf11837568cb8513a43d78a0921d1fae34dbc6d9859",
    "src/billgen/themes/chase/editor_business/replay_business.json": "09bfca27fd0fdb55951633d1da8e020add42935a2d6ef3d45711b3c72e385210",
    "src/billgen/themes/usbank/editor_us/data_us.json": "549b5fe6776c79fb76d002627d44b99336a2b39c22b23dd8af78095278c7afcc",
    "src/billgen/themes/usbank/editor_biz/data_biz.json": "2cdeb8a3cbda72d2c541d606085c6242d6298a8a12800f9d6ced713ecb85aea0",
    "src/billgen/themes/usbank/replay_us.json": "032bce3e89bd47e013567b5fedb0ff7978a7cb3b15c936221e0136be05cf82d6",
    "src/billgen/themes/usbank/replay_biz.json": "2cdeb8a3cbda72d2c541d606085c6242d6298a8a12800f9d6ced713ecb85aea0",
    "src/billgen/themes/wellsfargo/data_sample.json": "def7c740f624c732824b315bbbf6f20477b45af2c152298250aea9c6b11edd14",
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
