"""在支持的平台上定位字体，避免绑定某个人的电脑目录。"""
import os
from pathlib import Path


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
