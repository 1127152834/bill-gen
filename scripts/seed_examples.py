"""首次启动时写入合成示例，已有编辑数据保持不变。"""
import json
import sys
from pathlib import Path, PurePosixPath

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from runtime_paths import data_path


def seed_examples(root=ROOT):
    root=root.resolve()
    entries=json.loads((root/'examples/manifest.json').read_text(encoding='utf-8'))['files']
    plan=[]
    for entry in entries:
        source,target=PurePosixPath(entry['source']),PurePosixPath(entry['target'])
        if source.is_absolute() or target.is_absolute() or '..' in source.parts or '..' in target.parts or '\\' in str(source) or '\\' in str(target):
            raise ValueError('示例配置路径必须在项目内')
        if source.parts[0]!='examples' or source.suffix!='.json' or target.suffix!='.json' or not target.name.startswith('data'):
            raise ValueError('只能初始化账单数据 JSON')
        source_path,target_path=(root/str(source)).resolve(),Path(data_path(str(target),root=root))
        if not source_path.is_relative_to(root):raise ValueError('示例路径不能通过链接跳出项目')
        if target_path.exists():continue
        content=source_path.read_text(encoding='utf-8')
        json.loads(content)
        plan.append((target_path,content))
    copied=0
    for target_path,content in plan:
        try:
            with target_path.open('x',encoding='utf-8') as target_file:target_file.write(content)
            copied+=1
        except FileExistsError:pass
    return copied


if __name__=='__main__':print(f'已初始化 {seed_examples()} 份合成示例；已有数据未覆盖。')
