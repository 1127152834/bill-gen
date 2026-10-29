import json
import tempfile
import unittest
from pathlib import Path

from scripts.assets import import_assets, missing_assets


class AssetImportTests(unittest.TestCase):
    def setup_roots(self, temp, paths):
        source, target = Path(temp)/'source', Path(temp)/'target'
        source.mkdir();target.mkdir();(target/'scripts').mkdir()
        (target/'scripts/private-assets.json').write_text(json.dumps({'files':paths}))
        return source,target

    def test_copies_only_declared_assets_and_never_customer_records(self):
        with tempfile.TemporaryDirectory() as temp:
            source,target=self.setup_roots(temp,['bank/assets/font.ttf'])
            (source/'bank/assets').mkdir(parents=True)
            (source/'bank/assets/font.ttf').write_bytes(b'fixture-font')
            (source/'private-record.json').write_text('do not copy')
            self.assertEqual(missing_assets(target),['bank/assets/font.ttf'])
            self.assertEqual(import_assets(source,target),1)
            self.assertEqual(missing_assets(target),[])
            self.assertFalse((target/'private-record.json').exists())

    def test_rejects_traversal_source_code_and_symlink_escape(self):
        for path in ['../secret.pdf','/tmp/secret.pdf','run.py','bank/editor/data.json']:
            with self.subTest(path=path),tempfile.TemporaryDirectory() as temp:
                source,target=self.setup_roots(temp,[path])
                with self.assertRaises(ValueError):import_assets(source,target)
        with tempfile.TemporaryDirectory() as temp:
            source,target=self.setup_roots(temp,['bank/assets/orig.pdf'])
            outside=Path(temp)/'private';outside.mkdir();(outside/'orig.pdf').write_bytes(b'private')
            (source/'bank').mkdir();(source/'bank/assets').symlink_to(outside,target_is_directory=True)
            with self.assertRaises(ValueError):import_assets(source,target)

    def test_missing_assets_fail_before_any_copy_and_existing_different_files_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            source,target=self.setup_roots(temp,['bank/assets/a.pdf','bank/assets/b.pdf'])
            (source/'bank/assets').mkdir(parents=True);(source/'bank/assets/a.pdf').write_bytes(b'a')
            with self.assertRaises(FileNotFoundError):import_assets(source,target)
            self.assertFalse((target/'bank/assets/a.pdf').exists())
        with tempfile.TemporaryDirectory() as temp:
            source,target=self.setup_roots(temp,['bank/assets/a.pdf'])
            for root,value in [(source,b'new'),(target,b'existing')]:
                (root/'bank/assets').mkdir(parents=True);(root/'bank/assets/a.pdf').write_bytes(value)
            with self.assertRaises(FileExistsError):import_assets(source,target)
            self.assertEqual((target/'bank/assets/a.pdf').read_bytes(),b'existing')
