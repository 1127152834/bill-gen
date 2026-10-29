import json
from pathlib import Path
import tempfile
import unittest
from scripts.seed_examples import seed_examples


class ExampleTests(unittest.TestCase):
    def test_initializes_once_and_preserves_user_edits(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'examples').mkdir()
            (root/'examples/fixture.json').write_text('{"name":"Example"}')
            (root/'examples/manifest.json').write_text(json.dumps({'files':[{'source':'examples/fixture.json','target':'bank/editor/data.json'}]}))
            self.assertEqual(seed_examples(root),1)
            target=root/'bank/editor/data.json';target.write_text('{"name":"User edit"}')
            self.assertEqual(seed_examples(root),0)
            self.assertEqual(json.loads(target.read_text())['name'],'User edit')

    def test_invalid_manifest_cannot_overwrite_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'examples').mkdir()
            (root/'examples/manifest.json').write_text(json.dumps({'files':[{'source':'examples/fixture.json','target':'run.py'}]}))
            with self.assertRaises(ValueError):seed_examples(root)
