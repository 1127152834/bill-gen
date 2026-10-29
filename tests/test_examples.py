import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime_paths import data_path
from scripts.seed_examples import seed_examples


class ExampleTests(unittest.TestCase):
    def test_external_data_volume_seeds_once_and_preserves_existing_edits(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'source';(root/'examples').mkdir(parents=True)
            volume=Path(temp)/'volume'
            (root/'examples/fixture.json').write_text('{"name":"Example"}')
            (root/'examples/manifest.json').write_text(json.dumps({'files':[{'source':'examples/fixture.json','target':'bank/editor/data.json'}]}))
            with patch.dict('os.environ',{'BILL_GEN_DATA_DIR':str(volume)}):
                self.assertEqual(seed_examples(root),1)
                target=Path(data_path('bank/editor/data.json',root=root))
                self.assertEqual(target,(volume/'bank/editor/data.json').resolve())
                self.assertFalse((root/'bank').exists())
                target.write_text('{"name":"Saved edit"}')
                self.assertEqual(seed_examples(root),0)
                self.assertEqual(json.loads(target.read_text())['name'],'Saved edit')

    def test_runtime_path_rejects_absolute_traversal_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);volume=root/'volume';volume.mkdir()
            (volume/'bank').symlink_to(root/'outside',target_is_directory=True)
            with patch.dict('os.environ',{'BILL_GEN_DATA_DIR':str(volume)}):
                for name in ['/tmp/data.json','../data.json','bank/data.json','bank\\data.json']:
                    with self.subTest(name=name),self.assertRaises(ValueError):data_path(name,root=root)
            with patch.dict('os.environ',{'BILL_GEN_DATA_DIR':'relative-volume'}):
                with self.assertRaises(ValueError):data_path('bank/data.json',root=root)

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
