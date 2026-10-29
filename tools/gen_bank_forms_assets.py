"""把每套模板的「可填空表单样例」导出到 `flow/skills/bank-forms/assets/`。

    python tools/gen_bank_forms_assets.py            # 覆盖写
    python tools/gen_bank_forms_assets.py --check    # 只比对，不写；有漂移退出码 1

技能里的 assets 必须和引擎的默认数据同源，否则模型照着旧样例填，字段名早改了。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from billgen import registry  # noqa: E402

OUT = ROOT / "flow/skills/bank-forms/assets"
SKIP = {"metadata"}  # 输出用，不是表单字段


def payload(template_id: str) -> str:
    data = registry.get_renderer(template_id).default_data()
    trimmed = {k: v for k, v in data.items() if k not in SKIP}
    return json.dumps(trimmed, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="只检查是否与引擎默认数据一致")
    args = parser.parse_args(argv)

    OUT.mkdir(parents=True, exist_ok=True)
    drifted: list[str] = []
    for template_id in registry.SPEC_BY_ID:
        try:
            text = payload(template_id)
        except AssertionError:
            continue  # web 模板没有模块表单，跳过
        path = OUT / f"{template_id}.json"
        if args.check:
            if not path.exists() or path.read_text() != text:
                drifted.append(template_id)
            continue
        path.write_text(text)
        print(f"{template_id:18} -> {path.relative_to(ROOT)}")
    if args.check:
        for name in drifted:
            print(f"drifted: {name}")
        print("assets 与引擎默认数据一致" if not drifted else f"{len(drifted)} 份 assets 需要重生成")
        return 1 if drifted else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
