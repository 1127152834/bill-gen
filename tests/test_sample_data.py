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
    "src/billgen/themes/boa/editor/data_personal.json": "d5d9e39867fea8a2dede4cfbdc54dc4e7bd2a7cbe432ab12c751140536684f18",
    "src/billgen/themes/boa/editor/data_business.json": "de2b050c7c251ddeb7bfe859e4ead7d83daa921de3edafb95a5792e1ecb8f58f",
    "src/billgen/themes/chase/editor_personal/data_personal.json": "cdc595b9c9237e1cda903063463e97c788789740d852a9f14fadbff04928a9f5",
    "src/billgen/themes/chase/editor_business/data_business.json": "09bfca27fd0fdb55951633d1da8e020add42935a2d6ef3d45711b3c72e385210",
    "src/billgen/themes/chase/editor_personal/replay_personal.json": "cdc595b9c9237e1cda903063463e97c788789740d852a9f14fadbff04928a9f5",
    "src/billgen/themes/chase/editor_business/replay_business.json": "09bfca27fd0fdb55951633d1da8e020add42935a2d6ef3d45711b3c72e385210",
    "src/billgen/themes/usbank/editor_us/data_us.json": "94c9d222727066192ca64e547f3a107f0aaa83639c83cab4e2e59bd6c76cc6e5",
    "src/billgen/themes/usbank/editor_biz/data_biz.json": "dd34501b8510b64bc1fd833160f5f3d6ff75498d6ef36002b6ce0f312b360b81",
    "src/billgen/themes/usbank/replay_us.json": "b6b166b76fe5d719e552b3cd539d9db3a1b59514a6768cc806b88db3dcf7aae9",
    "src/billgen/themes/usbank/replay_biz.json": "dd34501b8510b64bc1fd833160f5f3d6ff75498d6ef36002b6ce0f312b360b81",
    "src/billgen/themes/wellsfargo/data_sample.json": "51579aef378120202ae1eeceda11b597585e46928819966796f09c6395182a53",
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
