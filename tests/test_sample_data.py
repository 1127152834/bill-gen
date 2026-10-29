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
    "src/billgen/themes/boa/editor/data_personal.json": "46357343c7225112126f8dedc91ac5d0882ea2adebda36ee1220ebc042f083cc",
    "src/billgen/themes/boa/editor/data_business.json": "e34048a98fa77b42728b63530e4c252fce737d32f32c62a0f5377c3b09311b96",
    "src/billgen/themes/chase/editor_personal/data_personal.json": "4ad2fdbae98b332292b2471f2cfbdd182fe7165102da756cd9203b7a56eefbeb",
    "src/billgen/themes/chase/editor_business/data_business.json": "ccd67121984977531e048c4b9a0cb616851bd4a7c61ae333f95bf197cfa77774",
    "src/billgen/themes/chase/editor_personal/replay_personal.json": "4ad2fdbae98b332292b2471f2cfbdd182fe7165102da756cd9203b7a56eefbeb",
    "src/billgen/themes/chase/editor_business/replay_business.json": "ccd67121984977531e048c4b9a0cb616851bd4a7c61ae333f95bf197cfa77774",
    "src/billgen/themes/usbank/editor_us/data_us.json": "4579a6213cc7bbb77c244ebecda636dc55baf8796765cbb7501231d1be06920d",
    "src/billgen/themes/usbank/editor_biz/data_biz.json": "a83002bb07500053120eca05c2e59faad4168b2179382ba3646fc44b030d0bda",
    "src/billgen/themes/usbank/replay_us.json": "4579a6213cc7bbb77c244ebecda636dc55baf8796765cbb7501231d1be06920d",
    "src/billgen/themes/usbank/replay_biz.json": "a83002bb07500053120eca05c2e59faad4168b2179382ba3646fc44b030d0bda",
    "src/billgen/themes/wellsfargo/data_sample.json": "b698f155cb27b7818eee23d2e712ac8d589c368ae1892a59d3aa290cf4f91317",
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
