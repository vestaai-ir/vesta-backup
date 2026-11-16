# Copyright 2026 Vesta AI (https://vestaai.ir)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).

import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from unittest.mock import MagicMock, patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged
from odoo.tools import config as odoo_config


@tagged("post_install", "-at_install")
class TestVestaBackup(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.root = tempfile.mkdtemp()
        cls.addClassCleanup(shutil.rmtree, cls.root, ignore_errors=True)
        cls.startClassPatcher(patch.dict(odoo_config.options, {"vesta_backup_dir": cls.root}))
        cls.startClassPatcher(patch.object(type(cls.env["ir.cron"]), "_commit_progress", return_value=1.0))
        cls.Config = cls.env["vesta.backup.config"]
        cls.local = cls.Config.create(
            {
                "name": "Disk",
                "destination": "local",
                "backup_format": "separate",
                "scheduled": False,
                "keep_count": 2,
            }
        )
        cls.s3 = cls.Config.create(
            {
                "name": "MinIO",
                "destination": "s3",
                "s3_endpoint_url": "http://minio:9000",
                "s3_bucket": "backups",
                "s3_access_key": "key",
                "s3_secret_key": "secret",
                "scheduled": False,
            }
        )

    def _run(self, config, **values):
        job = self.env["vesta.backup.job"].create({"config_id": config.id, **values})
        job._execute()
        return job

    def test_local_separate(self):
        job = self._run(self.local)
        self.assertEqual(job.state, "done", job.error)
        self.assertEqual(sorted(job.file_ids.mapped("kind")), ["database", "filestore", "manifest"])
        for file in job.file_ids:
            path = file._local_path()
            self.assertTrue(path and os.path.isfile(path))
            self.assertEqual(os.path.getsize(path), file.size)
        self.assertTrue(job.storage_path.startswith(os.path.realpath(self.root)))
        self.assertGreater(job.size, 0)

    def test_local_zip(self):
        self.local.write({"backup_format": "zip", "include_filestore": False})
        job = self._run(self.local)
        self.assertEqual(job.state, "done", job.error)
        self.assertEqual(job.file_ids.kind, "zip")
        with zipfile.ZipFile(job.file_ids._local_path()) as zf:
            self.assertEqual(sorted(zf.namelist()), ["dump.sql", "manifest.json"])

    def test_retention_and_delete(self):
        jobs = [self._run(self.local) for _i in range(3)]
        oldest_folder = jobs[0].storage_path
        self.assertEqual(jobs[0].state, "deleted")
        self.assertFalse(jobs[0].file_ids)
        self.assertFalse(os.path.exists(oldest_folder))
        self.assertEqual([j.state for j in jobs[1:]], ["done", "done"])
        folder = jobs[2].storage_path
        jobs[2].unlink()
        self.assertFalse(os.path.exists(folder))

    def test_local_path_confined(self):
        job = self._run(self.local)
        file = job.file_ids[:1]
        file.location = "/etc/passwd"
        self.assertFalse(file._local_path())
        file.location = os.path.join(job.storage_path, "..", "..", "..", "secret")
        self.assertFalse(file._local_path())

    def test_s3_upload(self):
        client = MagicMock()
        with patch.object(type(self.Config), "_s3_client", return_value=client):
            job = self._run(self.s3)
        self.assertEqual(job.state, "done", job.error)
        self.assertEqual(client.upload_file.call_count, 3)
        prefix = f"odoo-backups/{self.env.cr.dbname}/{job._backup_folder()}/{self.env.cr.dbname}_"
        for file in job.file_ids:
            self.assertTrue(file.location.startswith(prefix))
            self.assertTrue(file.location.endswith(file.name))
        _path, bucket, key = client.upload_file.call_args.args
        self.assertEqual(bucket, "backups")
        self.assertIn("sha256", client.upload_file.call_args.kwargs["ExtraArgs"]["Metadata"])
        # The temporary files are gone
        self.assertFalse([n for n in os.listdir(os.path.join(self.root, self.env.cr.dbname)) if n.startswith(".tmp")])

    def test_s3_failure_cleans_up(self):
        client = MagicMock()
        client.upload_file.side_effect = [None, RuntimeError("disk full")]
        with patch.object(type(self.Config), "_s3_client", return_value=client):
            job = self._run(self.s3)
        self.assertEqual(job.state, "failed")
        self.assertIn("disk full", job.error)
        self.assertFalse(job.file_ids)
        client.delete_object.assert_called_once()
        self.assertIn("Backup failed", self.s3.message_ids[:1].body)

    def test_s3_retention_deletes_objects(self):
        self.s3.keep_count = 1
        client = MagicMock()
        with patch.object(type(self.Config), "_s3_client", return_value=client):
            first = self._run(self.s3)
            keys = first.file_ids.mapped("location")
            self._run(self.s3)
        self.assertEqual(first.state, "deleted")
        deleted = client.delete_objects.call_args.kwargs["Delete"]["Objects"]
        self.assertEqual(sorted(o["Key"] for o in deleted), sorted(keys))

    def test_s3_missing_credentials(self):
        self.s3.s3_secret_key = False
        with self.assertRaises(UserError):
            self.s3._s3_client()

    def test_next_run(self):
        self.local.write({"interval_number": 1, "interval_type": "days", "next_run": datetime(2026, 1, 1, 23, 0)})
        self.assertEqual(self.local._get_next_run(datetime(2026, 1, 5, 10, 0)), datetime(2026, 1, 5, 23, 0))
        self.local.write({"interval_number": 6, "interval_type": "hours"})
        self.assertEqual(self.local._get_next_run(datetime(2026, 1, 2, 0, 30)), datetime(2026, 1, 2, 5, 0))

    def test_cron(self):
        self.Config.search([("id", "not in", (self.local | self.s3).ids)]).active = False
        self.local.write({"scheduled": True, "next_run": datetime(2026, 1, 1, 23, 0)})
        stale = self.env["vesta.backup.job"].create({"config_id": self.s3.id, "state": "running"})
        self.Config._cron_run_backups()
        self.assertEqual(stale.state, "failed")
        job = self.local.job_ids
        self.assertEqual(len(job), 1)
        self.assertEqual((job.trigger, job.state), ("scheduled", "done"))
        self.assertGreater(self.local.next_run, fields.Datetime.now())

    def test_backup_now_twice(self):
        self.local.action_backup_now()
        self.assertEqual(self.local.job_ids.state, "pending")
        with self.assertRaises(UserError):
            self.local.action_backup_now()

    def test_unlink_destination_with_backups(self):
        self._run(self.local)
        with self.assertRaises(UserError):
            self.local.unlink()
