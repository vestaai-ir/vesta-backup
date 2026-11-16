# Copyright 2026 Vesta AI (https://vestaai.ir)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).

import logging
import os
import shutil
import tempfile
import time

from odoo import _, api, fields, models
from odoo.addons.vesta_backup import archive, const
from odoo.exceptions import UserError
from odoo.fields import Command

_logger = logging.getLogger(__name__)

TMP_PREFIX = ".tmp-"


class VestaBackupJob(models.Model):
    _name = "vesta.backup.job"
    _description = "Backup"
    _order = "id desc"
    _rec_names_search = ["config_id"]

    name = fields.Char(compute="_compute_name")
    config_id = fields.Many2one(
        string="Destination", comodel_name="vesta.backup.config", required=True, ondelete="cascade", index=True
    )
    destination = fields.Selection(related="config_id.destination", string="Storage", store=True)
    backup_format = fields.Selection(
        string="Format",
        selection=const.BACKUP_FORMATS,
    )
    database = fields.Char(default=lambda self: self.env.cr.dbname, readonly=True)
    trigger = fields.Selection(
        selection=[("manual", "Manual"), ("scheduled", "Scheduled")], default="manual", required=True
    )
    state = fields.Selection(
        string="Status",
        selection=const.JOB_STATES,
        default="pending",
        required=True,
        index=True,
    )
    date_start = fields.Datetime(string="Started", default=fields.Datetime.now)
    date_end = fields.Datetime(string="Finished")
    duration = fields.Float(string="Duration (s)", digits=(16, 1))
    storage_path = fields.Char(string="Location", readonly=True, help="Folder of the files on the storage.")
    file_ids = fields.One2many(string="Files", comodel_name="vesta.backup.file", inverse_name="job_id")
    size = fields.Float(string="Size (bytes)", compute="_compute_size", store=True)
    size_display = fields.Char(string="Size", compute="_compute_size", store=True)
    error = fields.Text(readonly=True)

    @api.depends("config_id.name", "date_start")
    @api.depends_context("tz")
    def _compute_name(self):
        for job in self:
            date = fields.Datetime.context_timestamp(job, job.date_start) if job.date_start else False
            job.name = f"{job.config_id.name or ''} {date.strftime('%Y-%m-%d %H:%M') if date else ''}".strip()

    @api.depends("file_ids.size")
    def _compute_size(self):
        for job in self:
            job.size = sum(job.file_ids.mapped("size"))
            job.size_display = self.env["vesta.backup.file"]._human_size(job.size)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_running(self):
        if self.filtered(lambda j: j.state == "running"):
            raise UserError(_("A running backup can't be deleted."))

    def unlink(self):
        self._delete_stored_files()
        return super().unlink()

    # ------------------------------------------------------------
    # Run
    # ------------------------------------------------------------

    @api.model
    def _local_root(self, create=False):
        root = archive.backup_root(self.env.cr.dbname)
        if create:
            os.makedirs(root, mode=0o700, exist_ok=True)
        return root

    @api.model
    def _fail_interrupted(self):
        interrupted = self.search([("state", "=", "running")])
        if interrupted:
            interrupted.write({"state": "failed", "date_end": fields.Datetime.now(), "error": _("Interrupted.")})
        root = self._local_root()
        if os.path.isdir(root):
            for name in os.listdir(root):
                if name.startswith(TMP_PREFIX):
                    shutil.rmtree(os.path.join(root, name), ignore_errors=True)

    def _execute(self):
        """Build the backup files, store them on the destination and apply the retention."""
        self.ensure_one()
        config = self.config_id
        started = time.monotonic()
        self.write({"state": "running", "date_start": fields.Datetime.now(), "backup_format": config.backup_format})
        self.env["ir.cron"]._commit_progress()
        _logger.info("Backup of %s to %s started", self.database, config.name)
        try:
            with self.env.cr.savepoint():
                files = self._build_and_store()
                self.write(
                    {
                        "state": "done",
                        "date_end": fields.Datetime.now(),
                        "duration": time.monotonic() - started,
                        "file_ids": [Command.create(vals) for vals in files],
                    }
                )
        except Exception as error:
            _logger.exception("Backup of %s to %s failed", self.database, config.name)
            message = config._s3_error(error) if config.destination == "s3" else str(error)
            self.write(
                {
                    "state": "failed",
                    "date_end": fields.Datetime.now(),
                    "duration": time.monotonic() - started,
                    "error": message,
                }
            )
            config.message_post(body=_("Backup failed: %s", message))
            return
        _logger.info("Backup of %s to %s done in %.0fs", self.database, config.name, self.duration)
        config._apply_retention()

    def _backup_basename(self):
        return f"{self.database}_{fields.Datetime.now().strftime('%Y%m%d_%H%M%S')}"

    def _backup_folder(self):
        """Folder of the backup files on the storage, unique per backup."""
        return f"{fields.Datetime.to_datetime(self.date_start).strftime('%Y%m%d_%H%M%S')}_{self.id}"

    def _build_files(self, folder):
        """Write the backup files in ``folder``; return a list of (kind, file name)."""
        config = self.config_id
        base = self._backup_basename()
        if config.backup_format == "zip":
            name = f"{base}.zip"
            archive.dump_zip(self.database, os.path.join(folder, name), with_filestore=config.include_filestore)
            return [("zip", name)]
        files = []
        name = f"{base}.dump"
        archive.dump_database(self.database, os.path.join(folder, name))
        files.append(("database", name))
        if config.include_filestore:
            name = f"{base}_filestore.tar.gz"
            archive.archive_filestore(self.database, os.path.join(folder, name))
            files.append(("filestore", name))
        name = f"{base}_manifest.json"
        archive.write_manifest(self.env.cr, os.path.join(folder, name))
        files.append(("manifest", name))
        return files

    def _build_and_store(self):
        config = self.config_id
        root = self._local_root(create=True)
        with tempfile.TemporaryDirectory(prefix=TMP_PREFIX, dir=root) as folder:
            built = self._build_files(folder)
            files = []
            for kind, name in built:
                path = os.path.join(folder, name)
                files.append(
                    {
                        "kind": kind,
                        "name": name,
                        "size": os.path.getsize(path),
                        "checksum": archive.file_sha256(path),
                    }
                )
            if config.destination == "local":
                target = os.path.join(root, self._backup_folder())
                os.makedirs(target, mode=0o700)
                for vals in files:
                    location = os.path.join(target, vals["name"])
                    shutil.move(os.path.join(folder, vals["name"]), location)
                    vals["location"] = location
                self.storage_path = target
            else:
                self._s3_upload(folder, files)
        return files

    def _s3_upload(self, folder, files):
        from boto3.s3.transfer import TransferConfig

        config = self.config_id
        client = config._s3_client()
        prefix = f"{config._s3_key_prefix()}/{self._backup_folder()}"
        transfer = TransferConfig(multipart_threshold=64 * 1024 * 1024, multipart_chunksize=64 * 1024 * 1024)
        uploaded = []
        try:
            for vals in files:
                key = f"{prefix}/{vals['name']}"
                client.upload_file(
                    os.path.join(folder, vals["name"]),
                    config.s3_bucket,
                    key,
                    ExtraArgs={"Metadata": {"sha256": vals["checksum"], "database": self.database}},
                    Config=transfer,
                )
                uploaded.append(key)
                vals["location"] = key
        except Exception:
            for key in uploaded:
                try:
                    client.delete_object(Bucket=config.s3_bucket, Key=key)
                except Exception:
                    _logger.warning("Could not delete the partial backup object %s", key, exc_info=True)
            raise
        self.storage_path = f"s3://{config.s3_bucket}/{prefix}/"

    # ------------------------------------------------------------
    # Retention and deletion
    # ------------------------------------------------------------

    def _delete_stored_files(self):
        """Delete the files of the backups from their storage; keep the history lines."""
        for job in self.filtered(lambda j: j.file_ids):
            config = job.config_id
            if job.destination == "s3":
                client = config._s3_client()
                keys = [{"Key": f.location} for f in job.file_ids if f.location]
                if keys:
                    client.delete_objects(Bucket=config.s3_bucket, Delete={"Objects": keys, "Quiet": True})
            else:
                for file in job.file_ids:
                    path = file._local_path()
                    if path and os.path.exists(path):
                        os.remove(path)
                folder = job.storage_path and os.path.realpath(job.storage_path)
                if folder and archive.is_inside(folder, self._local_root()) and os.path.isdir(folder):
                    shutil.rmtree(folder, ignore_errors=True)
            job.file_ids.unlink()
            job.state = "deleted"

    def action_delete_files(self):
        jobs = self.filtered(lambda j: j.state == "done")
        try:
            jobs._delete_stored_files()
        except Exception as error:
            raise UserError(_("Could not delete the files: %s", jobs.config_id[:1]._s3_error(error))) from error

    def action_download(self):
        self.ensure_one()
        if len(self.file_ids) == 1:
            return self.file_ids.action_download()
        return {
            "type": "ir.actions.act_window",
            "res_model": "vesta.backup.job",
            "res_id": self.id,
            "view_mode": "form",
        }
