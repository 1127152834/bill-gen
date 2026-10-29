"""检查仓库模板资产并补齐 LFS 内容；保留旧版本机导入命令。"""
import argparse
import json
from pathlib import Path, PurePosixPath
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
EXTENSIONS = {'.pdf','.ttf','.otf','.woff','.woff2','.ttc','.png','.jpg','.jpeg','.svg','.z','.css'}


def asset_paths(root=ROOT):
    entries=json.loads((root/'scripts/private-assets.json').read_text(encoding='utf-8'))['files']
    result=[]
    for value in entries:
        path=PurePosixPath(value)
        allowed=(path.suffix.lower() in EXTENSIONS or
                 (path.suffix=='.json' and ('assets' in path.parts or path.name.startswith(('layout_','replay_','orig_meta','prepared')))) or
                 (path.suffix=='.js' and ('vendor' in path.parts or path.name in {'fonts_b64.js','baked-lines.js','font-decls.js','logo-svg.js','private-assets.js'})))
        if path.is_absolute() or '..' in path.parts or '\\' in value or not allowed:
            raise ValueError('资产清单包含不允许的路径')
        if value in result:raise ValueError('资产清单包含重复路径')
        result.append(value)
    return result


def safe_asset_path(root, name):
    root=Path(root).resolve()
    path=(root/name).resolve()
    if not path.is_relative_to(root):raise ValueError('资产路径不能通过链接跳出项目目录')
    return path


def missing_assets(root=ROOT):
    from scripts.lfs_assets import read_pointer
    return [name for name in asset_paths(root) if not (path:=safe_asset_path(root,name)).is_file() or read_pointer(path)]


def import_assets(source,root=ROOT):
    source,root=source.resolve(strict=True),root.resolve(strict=True)
    manifest=json.loads((root/'scripts/private-assets.json').read_text(encoding='utf-8'))
    derived={}
    if manifest.get('derived'):
        if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
        from scripts.extract_private import extract_assets
        derived=extract_assets(source)
        if set(derived)!=set(manifest['derived']):raise ValueError('派生资产与清单不一致')
    plan=[]
    for name in asset_paths(root):
        outgoing=(root/name).resolve()
        if not outgoing.is_relative_to(root):
            raise ValueError('资产路径不能通过链接跳出项目目录')
        if name in derived:
            content=derived[name]
        else:
            incoming=(source/name).resolve(strict=True)
            if not incoming.is_relative_to(source):raise ValueError('资产路径不能通过链接跳出项目目录')
            if not incoming.is_file():raise FileNotFoundError(name)
            content=incoming.read_bytes()
        if outgoing.exists():
            if not outgoing.is_file() or content!=outgoing.read_bytes():raise FileExistsError(f'保留已有文件：{name}')
            continue
        plan.append((content,outgoing))
    for content,outgoing in plan:
        outgoing.parent.mkdir(parents=True,exist_ok=True)
        outgoing.write_bytes(content)
    return len(plan)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    load=commands.add_parser('import');load.add_argument('source',type=Path,help='原始整合版目录')
    commands.add_parser('check')
    commands.add_parser('hydrate')
    args=parser.parse_args()
    try:
        if args.command=='import':print(f'已导入 {import_assets(args.source)} 个本机资产文件。')
        if args.command=='hydrate':
            from scripts.lfs_assets import hydrate_assets
            print(f'已补齐 {hydrate_assets()} 个公共 LFS 模板资源。')
        missing=missing_assets()
        if missing:
            print(f'有 {len(missing)} 个模板资源未就绪；请完整克隆仓库并运行 python3 scripts/assets.py hydrate')
            raise SystemExit(1)
        print('模板资产检查通过。')
    except (OSError,ValueError,KeyError) as error:parser.exit(1,f'{error}\n')


if __name__=='__main__':main()
