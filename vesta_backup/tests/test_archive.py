# Copyright 2026 Vesta AI (https://vestaai.ir)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).

import json
import os
import subprocess
import tarfile
import tempfile

from odoo.addons.vesta_backup import archive
from odoo.tests import TransactionCase, tagged
from odoo.tools.misc import exec_pg_environ, find_pg_tool


@tagged("post_install", "-at_install")
class TestArchive(TransactionCase):
    def setUp(self):
        super().setUp()
        self.folder = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", self.folder], check=False))

    def test_is_inside(self):
        root = os.path.realpath(self.folder)
        self.assertTrue(archive.is_inside(os.path.join(root, "a", "b.zip"), root))
        self.assertFalse(archive.is_inside(root, root))
        self.assertFalse(archive.is_inside(os.path.join(root, "..", "etc", "passwd"), root))
        self.assertFalse(archive.is_inside(root + "-other/file", root))

    def test_dump_database(self):
        path = os.path.join(self.folder, "db.dump")
        archive.dump_database(self.env.cr.dbname, path)
        listing = subprocess.run(
            [find_pg_tool("pg_restore"), "--list", path], env=exec_pg_environ(), capture_output=True, check=True
        ).stdout.decode()
        self.assertIn("TABLE public res_partner", listing)

    def test_dump_database_error(self):
        with self.assertRaises(archive.BackupError):
            archive.dump_database("vesta_backup_no_such_database", os.path.join(self.folder, "db.dump"))

    def test_archive_filestore(self):
        path = os.path.join(self.folder, "filestore.tar.gz")
        archive.archive_filestore(self.env.cr.dbname, path)
        with tarfile.open(path) as tar:
            self.assertEqual(tar.getnames()[0], "filestore")

    def test_manifest_and_checksum(self):
        path = os.path.join(self.folder, "manifest.json")
        archive.write_manifest(self.env.cr, path)
        with open(path, encoding="utf-8") as stream:
            manifest = json.load(stream)
        self.assertEqual(manifest["db_name"], self.env.cr.dbname)
        self.assertIn("vesta_backup", manifest["modules"])
        self.assertEqual(len(archive.file_sha256(path)), 64)
