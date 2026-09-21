import importlib.util
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

spec = importlib.util.spec_from_file_location('backup_local', Path(__file__).resolve().parents[1] / 'tools/backup_local.py')
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)

class BackupTests(unittest.TestCase):
    def test_roundtrip_tampering_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / 'source.db'
            with closing(sqlite3.connect(db)) as connection:
                connection.execute('CREATE TABLE events(id INTEGER)')
                connection.execute('INSERT INTO events VALUES(7)')
                connection.commit()
            evidence = root / 'evidence'
            evidence.mkdir()
            (evidence / 'frame.jpg').write_bytes(b'fixture evidence')
            target = root / 'snapshot'
            backup.capture(db,evidence,target)
            self.assertTrue(backup.verify(target))
            restored = backup.restore(target,root / 'recovery-stage')
            with closing(sqlite3.connect(restored / 'database.db')) as connection:
                self.assertEqual(connection.execute('SELECT id FROM events').fetchone()[0],7)
            with self.assertRaises(ValueError):
                backup.restore(target,restored)
            with self.assertRaises(FileExistsError):
                backup.capture(db,evidence,target)
            with self.assertRaises(ValueError):
                backup.capture(db,evidence,evidence / 'nested-backup')
            (target / 'evidence/frame.jpg').write_bytes(b'altered')
            with self.assertRaises(ValueError):
                backup.verify(target)
            with self.assertRaises(ValueError):
                backup.restore(target,root / 'bad-restore')
