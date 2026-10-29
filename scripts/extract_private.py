"""Extract allowlisted private constants without executing template source code.

The returned bytes belong in ignored local storage. No source or output file is
written here, and errors deliberately omit source literals.
"""
import ast
import json
import math
from pathlib import Path
import re


ALLY_CONSTANTS = (
    'LOGO_DEFAULT', 'P4_BORDER_L', 'P4_BORDER_R', 'PANEL_PNG_SRC',
    'HOUSE_PNG', 'P4_STATIC', 'WT', 'TRK', 'SPACE_SPEC', 'ORDER_SPEC',
    'WIN_DIFFS', 'SPECS',
)
DERIVED_PATHS = (
    '富国/statement_template/assets/declared_fonts.json',
    'ally bank/private-assets.js',
)
_MAX_SOURCE_BYTES = 8 * 1024 * 1024
_NUMBER = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?')
_IDENTIFIER = re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*')


def _read(source: Path, relative: str) -> str:
    path = (source / relative).resolve(strict=True)
    if not path.is_relative_to(source):
        raise ValueError('Private asset source escapes the selected directory')
    if not path.is_file() or path.stat().st_size > _MAX_SOURCE_BYTES:
        raise ValueError('Private asset source is missing or too large')
    return path.read_text(encoding='utf-8')


class _Literal:
    """Small, non-evaluating parser for JS object/array/scalar literals only."""

    def __init__(self, text: str, position: int):
        self.text = text
        self.pos = position

    def space(self):
        while self.pos < len(self.text):
            if self.text[self.pos].isspace():
                self.pos += 1
            elif self.text.startswith('//', self.pos):
                end = self.text.find('\n', self.pos)
                self.pos = len(self.text) if end < 0 else end + 1
            elif self.text.startswith('/*', self.pos):
                end = self.text.find('*/', self.pos + 2)
                if end < 0:
                    raise ValueError('Unterminated private asset comment')
                self.pos = end + 2
            else:
                break

    def take(self, token: str) -> bool:
        self.space()
        if self.text.startswith(token, self.pos):
            self.pos += len(token)
            return True
        return False

    def string(self):
        quote = self.text[self.pos]
        self.pos += 1
        result = []
        escapes = {'n': '\n', 'r': '\r', 't': '\t', 'b': '\b', 'f': '\f',
                   'v': '\v', '\\': '\\', '/': '/', "'": "'", '"': '"'}
        while self.pos < len(self.text):
            char = self.text[self.pos]
            self.pos += 1
            if char == quote:
                return ''.join(result)
            if char in '\n\r' or ord(char) < 32:
                raise ValueError('Invalid private asset string')
            if char == '\\':
                if self.pos >= len(self.text):
                    break
                char = self.text[self.pos]
                self.pos += 1
                if char in escapes:
                    char = escapes[char]
                elif char in ('u', 'x'):
                    count = 4 if char == 'u' else 2
                    digits = self.text[self.pos:self.pos + count]
                    if len(digits) != count or not re.fullmatch('[0-9a-fA-F]+', digits):
                        raise ValueError('Invalid private asset string escape')
                    self.pos += count
                    char = chr(int(digits, 16))
                else:
                    raise ValueError('Unsupported private asset string escape')
            result.append(char)
        raise ValueError('Unterminated private asset string')

    def value(self, depth=0):
        if depth > 64:
            raise ValueError('Private asset nesting limit exceeded')
        self.space()
        char = self.text[self.pos:self.pos + 1]
        if char in ('"', "'"):
            return self.string()
        if self.take('{'):
            result = {}
            if self.take('}'):
                return result
            while True:
                self.space()
                if self.text[self.pos:self.pos + 1] in ('"', "'"):
                    key = self.string()
                else:
                    match = _IDENTIFIER.match(self.text, self.pos)
                    if not match:
                        raise ValueError('Invalid private asset object key')
                    key, self.pos = match.group(), match.end()
                if key in result or not self.take(':'):
                    raise ValueError('Duplicate or invalid private asset object key')
                result[key] = self.value(depth + 1)
                if self.take('}'):
                    return result
                if not self.take(','):
                    raise ValueError('Invalid private asset object')
                if self.take('}'):
                    return result
        if self.take('['):
            result = []
            if self.take(']'):
                return result
            while True:
                result.append(self.value(depth + 1))
                if self.take(']'):
                    return result
                if not self.take(','):
                    raise ValueError('Invalid private asset array')
                if self.take(']'):
                    return result
        match = _NUMBER.match(self.text, self.pos)
        if match:
            self.pos = match.end()
            result = json.loads(match.group())
            if isinstance(result, float) and not math.isfinite(result):
                raise ValueError('Nonfinite private asset number')
            return result
        match = _IDENTIFIER.match(self.text, self.pos)
        if match and match.group() in ('true', 'false', 'null'):
            self.pos = match.end()
            return {'true': True, 'false': False, 'null': None}[match.group()]
        raise ValueError('Private asset initializer must be a static literal')


def _ally_assets(text: str) -> bytes:
    assets = {}
    for name in ALLY_CONSTANTS:
        pattern = r'(?m)^[ \t]*(?:var|const|let)[ \t]+' + name + r'[ \t]*='
        matches = list(re.finditer(pattern, text))
        if len(matches) != 1:
            raise ValueError(f'Missing or duplicate private asset constant: {name}')
        parser = _Literal(text, matches[0].end())
        try:
            assets[name] = parser.value()
            if not parser.take(';'):
                raise ValueError('Private asset declaration requires a terminator')
            # Do not silently accept executable text appended to the declaration.
            line_end = text.find('\n', parser.pos)
            tail = text[parser.pos:line_end if line_end >= 0 else len(text)].strip()
            tail = re.sub(r'/\*.*?\*/', '', tail).strip()
            if tail and not tail.startswith('//'):
                raise ValueError('Unexpected private asset declaration suffix')
        except (ValueError, IndexError, RecursionError):
            raise ValueError(f'Invalid static private asset constant: {name}') from None
    # 历史文本排序缓存含原账单内容，导入资源时也不能恢复它。
    assets['ORDER_SPEC'] = {}
    # JSON.parse avoids JS object-literal special behavior for __proto__ keys.
    payload = json.dumps(assets, ensure_ascii=True, separators=(',', ':'), allow_nan=False)
    return ('window.BILL_GEN_ALLY_ASSETS = JSON.parse(' + json.dumps(payload) + ');\n').encode()


def _declared_fonts(text: str) -> bytes:
    try:
        tree = ast.parse(text)
        values = [node.value for node in tree.body if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == 'ASSETS' for target in node.targets)]
        if len(values) != 1:
            raise ValueError('Missing or duplicate font asset assignment')
        value = values[0]
        if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
                and isinstance(value.func.value, ast.Name) and value.func.value.id == 'json'
                and value.func.attr == 'loads' and len(value.args) == 1 and not value.keywords):
            raise ValueError('Font asset assignment must contain literal JSON')
        encoded = ast.literal_eval(value.args[0])
        if not isinstance(encoded, str):
            raise ValueError('Font asset JSON must be a string literal')
        assets = json.loads(encoded)
        if not isinstance(assets, dict):
            raise ValueError('Font asset JSON must be an object')
        assets = {key: assets[key] for key in ('fonts', 'encoding') if key in assets}
        return (json.dumps(assets, ensure_ascii=True, separators=(',', ':'), allow_nan=False) + '\n').encode()
    except (SyntaxError, ValueError, TypeError, RecursionError):
        raise ValueError('Invalid static font asset declaration') from None


def extract_assets(source: Path) -> dict[str, bytes]:
    """Return two derived private files; never execute code or write to disk."""
    source = source.resolve(strict=True)
    return {
        DERIVED_PATHS[0]: _declared_fonts(_read(source, '富国/statement_template/declared_fonts.py')),
        DERIVED_PATHS[1]: _ally_assets(_read(source, 'ally bank/editor.html')),
    }
