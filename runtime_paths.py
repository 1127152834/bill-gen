"""在支持的平台上定位字体，避免绑定某个人的电脑目录。"""
import os
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent


def data_path(relative, root=ROOT):
    """容器把可写数据与镜像代码分开，本机仍兼容原目录结构。"""
    relative=PurePosixPath(relative)
    if relative.is_absolute() or '..' in relative.parts or '\\' in str(relative) or not relative.parts:
        raise ValueError('运行数据路径必须是项目内的相对路径')
    configured=os.environ.get('BILL_GEN_DATA_DIR')
    if configured and not Path(configured).is_absolute():raise ValueError('BILL_GEN_DATA_DIR 必须是绝对路径')
    base=Path(configured).resolve() if configured else Path(root).resolve()
    target=(base/relative).resolve()
    if not target.is_relative_to(base):raise ValueError('运行数据路径不能通过链接跳出数据目录')
    target.parent.mkdir(parents=True,exist_ok=True)
    return str(target)


def font_path(style='Regular'):
    configured=os.environ.get('STATEMENT_FONT_DIR')
    directories=([Path(configured)] if configured else [])+[Path('/usr/share/fonts/truetype/liberation2'),Path('/usr/share/fonts/truetype/liberation')]
    for directory in directories:
        candidate=directory/f'LiberationSans-{style}.ttf'
        if candidate.is_file():return str(candidate)
    suffix={'Regular':'','Bold':' Bold','Italic':' Italic','BoldItalic':' Bold Italic'}[style]
    candidate=Path('/System/Library/Fonts/Supplemental')/f'Arial{suffix}.ttf'
    if candidate.is_file():return str(candidate)
    raise RuntimeError('缺少字体：请安装 Liberation Sans，或设置 STATEMENT_FONT_DIR。')


def cjk_font_path():
    configured=os.environ.get('STATEMENT_CJK_FONT')
    candidates=([Path(configured)] if configured else [])+[
        Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'),
        Path('/System/Library/Fonts/Hiragino Sans GB.ttc'),
        Path('/System/Library/Fonts/STHeiti Light.ttc'),
    ]
    return next((str(p) for p in candidates if p.is_file()),None)
