import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


NAMES = ('LOGO_DEFAULT', 'P4_BORDER_L', 'P4_BORDER_R', 'PANEL_PNG_SRC',
         'HOUSE_PNG', 'P4_STATIC', 'WT', 'TRK', 'SPACE_SPEC', 'ORDER_SPEC',
         'WIN_DIFFS', 'SPECS')


class PrivateExtractionTests(unittest.TestCase):
    def extract(self, source):
        spec = importlib.util.find_spec('scripts.extract_private')
        self.assertIsNotNone(spec, 'private asset extractor has not been implemented')
        from scripts.extract_private import extract_assets
        return extract_assets(source)

    def fixture(self, root, expression=None, python_expression=None):
        wf = root / '富国/statement_template/declared_fonts.py'
        wf.parent.mkdir(parents=True)
        value = python_expression or repr('{"fonts":{"fixture":"encoded-font"},"meta":{}}')
        wf.write_text('import json\nASSETS = json.loads(' + value + ')\nraise RuntimeError("do not execute")\n')
        ally = root / 'ally bank/editor.html'
        ally.parent.mkdir()
        rows = ['var ' + name + ' = ' + (expression if name == 'TRK' and expression else '"fixture"') + ';' for name in NAMES]
        ally.write_text('<html><script>\n' + '\n'.join(rows) + '\nvar DEFAULT_DATA = {private: "NEVER_COPY_CUSTOMER"};\nthrow Error("do not execute");\n</script></html>')
        return ally, wf

    def test_extracts_literals_without_importing_modules_or_copying_editor_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root, "{a:[1,-2.5,true,null,], 'punctuation': 'x; } \\' y',}")
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            result = self.extract(root)
            self.assertEqual(set(result), {'富国/statement_template/assets/declared_fonts.json', 'ally bank/private-assets.js'})
            self.assertEqual(json.loads(result['富国/statement_template/assets/declared_fonts.json']), {'fonts': {'fixture': 'encoded-font'}})
            js = result['ally bank/private-assets.js'].decode()
            self.assertNotIn('NEVER_COPY_CUSTOMER', js)
            self.assertNotIn('throw Error', js)
            payload = json.loads(json.loads(js.removeprefix('window.BILL_GEN_ALLY_ASSETS = JSON.parse(').removesuffix(');\n')))
            self.assertEqual(payload['TRK'], {'a': [1, -2.5, True, None], 'punctuation': "x; } ' y"})
            self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_rejects_executable_or_incomplete_javascript_expressions(self):
        for expression in ['getAssets()', '{x: window.secret}', '{x: ()=>1}', '`template`', '{x:1}; run()', '{x:NaN}', '{x:1e999}', '{x:1,x:2}', '[1,,2]']:
            with self.subTest(expression=expression), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.fixture(root, expression)
                with self.assertRaises(ValueError):
                    self.extract(root)

    def test_rejects_python_computation_without_executing_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root, python_expression='__import__("os").getcwd()')
            with self.assertRaises(ValueError):
                self.extract(root)

    def test_accepts_trailing_block_comment_but_rejects_appended_code(self):
        for tail, valid in [(' /* fixture comment */', True),
                            (' /* fixture */ run();', False),
                            (' /* incomplete', False)]:
            with self.subTest(tail=tail), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                ally, _ = self.fixture(root)
                ally.write_text(ally.read_text().replace('var P4_STATIC = "fixture";',
                                                       'var P4_STATIC = "fixture";' + tail))
                if valid:
                    self.assertEqual(len(self.extract(root)), 2)
                else:
                    with self.assertRaises(ValueError):
                        self.extract(root)

    def test_missing_and_duplicate_constants_fail_closed(self):
        for change in ('missing', 'duplicate'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                ally, _ = self.fixture(root)
                text = ally.read_text()
                text = text.replace('var WT = "fixture";', '') if change == 'missing' else text.replace('var WT = "fixture";', 'var WT = "fixture";\nvar WT = "other";')
                ally.write_text(text)
                with self.assertRaises(ValueError):
                    self.extract(root)

    def test_rejects_source_symlink_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'source'
            root.mkdir()
            _, wf = self.fixture(root)
            other = Path(temp) / 'outside.py'
            other.write_bytes(wf.read_bytes())
            wf.unlink()
            wf.symlink_to(other)
            with self.assertRaises(ValueError):
                self.extract(root)
