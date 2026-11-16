# Copyright 2026 Vesta AI (https://vestaai.ir)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).

import logging

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.addons.vesta_backup import const
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

INTERVALS = {
    "hours": lambda n: relativedelta(hours=n),
    "days": lambda n: relativedelta(days=n),
    "weeks": lambda n: relativedelta(weeks=n),
    "months": lambda n: relativedelta(months=n),
}


class VestaBackupConfig(models.Model):
    _name = "vesta.backup.config"
    _description = "Backup Destination"
    _inherit = ["mail.thread"]
    _order = "sequence, id"

    name = fields.Char(required=True, tracking=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True, tracking=True)
    destination = fields.Selection(
        selection=[("s3", "S3 / MinIO"), ("local", "Server Disk")],
        required=True,
        default="s3",
        tracking=True,
        help="S3: any S3-compatible storage (Amazon S3, MinIO, ArvanCloud, Cloudflare R2, Wasabi, ...).\n"
        "Server Disk: keep the backups on this server, to download them to your computer.",
    )
    backup_format = fields.Selection(
        string="Format",
        selection=const.BACKUP_FORMATS,
        required=True,
        default="separate",
        tracking=True,
        help="Separately: a compressed pg_dump of the database (.dump) and a .tar.gz of the filestore.\n"
        "Single zip: the format of the Odoo database manager, restorable from /web/database/manager.",
    )
    include_filestore = fields.Boolean(
        string="Include Files",
        default=True,
        tracking=True,
        help="Back up the filestore (attachments, images, documents) with the database.",
    )

    # S3
    s3_endpoint_url = fields.Char(
        string="Endpoint URL",
        help="Leave empty for Amazon S3. MinIO example: http://minio:9000",
    )
    s3_region = fields.Char(string="Region", default="us-east-1")
    s3_bucket = fields.Char(string="Bucket", tracking=True)
    s3_prefix = fields.Char(
        string="Folder",
        default="odoo-backups",
        help="Prefix of the objects in the bucket; the database name is appended to it.",
    )
    s3_access_key = fields.Char(string="Access Key", groups="base.group_system", copy=False)
    s3_secret_key = fields.Char(string="Secret Key", groups="base.group_system", copy=False)
    s3_addressing_style = fields.Selection(
        string="Addressing Style",
        selection=[("path", "Path (MinIO)"), ("virtual", "Virtual Host (AWS)"), ("auto", "Automatic")],
        default="path",
        required=True,
    )
    s3_verify_ssl = fields.Boolean(string="Verify SSL Certificate", default=True)
    s3_download_proxy = fields.Boolean(
        string="Download Through Odoo",
        default=True,
        help="Stream downloads through this server. Uncheck to send the browser to a temporary signed link"
        " of the storage instead, if the storage is reachable from the users' computers.",
    )

    # Schedule
    scheduled = fields.Boolean(string="Automatic Backups", default=True, tracking=True)
    interval_number = fields.Integer(string="Every", default=1, tracking=True)
    interval_type = fields.Selection(
        string="Interval Unit",
        selection=[("hours", "Hours"), ("days", "Days"), ("weeks", "Weeks"), ("months", "Months")],
        default="days",
        required=True,
        tracking=True,
    )
    next_run = fields.Datetime(
        string="Next Backup",
        default=lambda self: fields.Datetime.now().replace(hour=23, minute=0, second=0, microsecond=0),
        tracking=True,
    )

    # Retention
    keep_count = fields.Integer(
        string="Keep Last",
        default=7,
        help="Number of successful backups to keep; older ones are deleted. 0 keeps all of them.",
    )
    keep_days = fields.Integer(
        string="Keep Days",
        default=0,
        help="Delete successful backups older than this number of days. 0 disables it."
        " The most recent backup is never deleted.",
    )

    job_ids = fields.One2many(string="Backups", comodel_name="vesta.backup.job", inverse_name="config_id")
    job_count = fields.Integer(compute="_compute_last_job")
    last_job_id = fields.Many2one(string="Last Backup", comodel_name="vesta.backup.job", compute="_compute_last_job")
    last_state = fields.Selection(
        string="Last Status",
        selection=const.JOB_STATES,
        compute="_compute_last_job",
    )
    last_success_date = fields.Datetime(string="Last Successful Backup", compute="_compute_last_job")
    stored_size = fields.Float(string="Stored Size (bytes)", compute="_compute_last_job")
    stored_size_display = fields.Char(string="Stored Size", compute="_compute_last_job")

    _interval_number_positive = models.Constraint("CHECK(interval_number > 0)", "The backup interval must be positive.")
    _retention_positive = models.Constraint(
        "CHECK(keep_count >= 0 AND keep_days >= 0)", "Retention values can't be negative."
    )

    @api.depends("job_ids.state", "job_ids.size")
    def _compute_last_job(self):
        Job = self.env["vesta.backup.job"]
        for config in self:
            jobs = Job.search([("config_id", "=", config.id)], order="id desc") if config.id else Job
            done = jobs.filtered(lambda j: j.state == "done")
            config.job_count = len(jobs)
            config.last_job_id = jobs[:1]
            config.last_state = jobs[:1].state
            config.last_success_date = done[:1].date_start
            config.stored_size = sum(done.mapped("size"))
            config.stored_size_display = self.env["vesta.backup.file"]._human_size(config.stored_size)

    @api.constrains("destination", "s3_bucket")
    def _check_s3_bucket(self):
        for config in self:
            if config.destination == "s3" and not config.s3_bucket:
                raise UserError(_("Enter the bucket of the S3 destination %s.", config.name))

    @api.model_create_multi
    def create(self, vals_list):
        configs = super().create(vals_list)
        configs._schedule_cron()
        return configs

    def write(self, vals):
        res = super().write(vals)
        if {"next_run", "scheduled", "active"} & set(vals):
            self._schedule_cron()
        return res

    @api.ondelete(at_uninstall=False)
    def _unlink_except_stored_backups(self):
        if self.job_ids.filtered(lambda j: j.state == "done"):
            raise UserError(
                _("This destination still has backups. Delete the backups first, or archive the destination.")
            )

    # ------------------------------------------------------------
    # Schedule
    # ------------------------------------------------------------

    def _schedule_cron(self):
        cron = self.env.ref("vesta_backup.ir_cron_vesta_backup", raise_if_not_found=False)
        if not cron:
            return
        dates = [c.next_run for c in self if c.active and c.scheduled and c.next_run]
        if dates:
            cron.sudo()._trigger(at=dates)

    def _get_next_run(self, now):
        """Next backup date after ``now``, keeping the time of day of the current schedule."""
        self.ensure_one()
        step = INTERVALS[self.interval_type](self.interval_number)
        next_run = self.next_run or now
        while next_run <= now:
            next_run += step
        return next_run

    @api.model
    def _cron_run_backups(self):
        """Run the due and the queued backups, one at a time, committing after each one."""
        Job = self.env["vesta.backup.job"]
        cron = self.env["ir.cron"]
        # Backups only run in this cron, which never runs twice at once: a running backup here was interrupted.
        Job._fail_interrupted()
        now = fields.Datetime.now()
        for config in self.search([("scheduled", "=", True), ("next_run", "<=", now)]):
            Job.create({"config_id": config.id, "trigger": "scheduled"})
            config.next_run = config._get_next_run(now)
        jobs = Job.search([("state", "=", "pending")], order="id")
        cron._commit_progress(remaining=len(jobs))
        for job in jobs:
            job._execute()
            if not cron._commit_progress(1):
                break

    def _apply_retention(self):
        """Delete the oldest successful backups beyond the retention rules (never the last one)."""
        now = fields.Datetime.now()
        for config in self:
            done = config.job_ids.filtered(lambda j: j.state == "done")
            done = done.sorted(lambda j: (j.date_start, j.id), reverse=True)
            expired = self.env["vesta.backup.job"]
            if config.keep_count:
                expired |= done[config.keep_count :]
            if config.keep_days:
                limit = now - relativedelta(days=config.keep_days)
                expired |= done[1:].filtered(lambda j, limit=limit: j.date_start < limit)
            if not expired:
                continue
            try:
                expired._delete_stored_files()
            except Exception:
                _logger.warning("Could not delete the expired backups of %s", config.name, exc_info=True)

    # ------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------

    def action_backup_now(self):
        self.ensure_one()
        if self.job_ids.filtered(lambda j: j.state in ("pending", "running")):
            raise UserError(_("A backup of this destination is already in progress."))
        self.env["vesta.backup.job"].create({"config_id": self.id, "trigger": "manual"})
        self.env.ref("vesta_backup.ir_cron_vesta_backup").sudo()._trigger()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "info",
                "title": _("Backup started"),
                "message": _("The backup runs in the background. Refresh the list of backups to follow it."),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def action_view_jobs(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("vesta_backup.action_vesta_backup_job")
        action["domain"] = [("config_id", "=", self.id)]
        action["context"] = {"default_config_id": self.id}
        return action

    def action_test_connection(self):
        self.ensure_one()
        if self.destination == "local":
            self.env["vesta.backup.job"]._local_root(create=True)
            message = _("The server disk folder is ready.")
        else:
            self._s3_check()
            message = _("Connected to the bucket %s and wrote a test file.", self.s3_bucket)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"type": "success", "title": _("Connection OK"), "message": message},
        }

    def action_s3_create_bucket(self):
        self.ensure_one()
        client = self._s3_client()
        try:
            params = {"Bucket": self.s3_bucket}
            if self.s3_region and self.s3_region != "us-east-1" and not self.s3_endpoint_url:
                params["CreateBucketConfiguration"] = {"LocationConstraint": self.s3_region}
            client.create_bucket(**params)
        except Exception as error:
            raise UserError(_("Could not create the bucket: %s", self._s3_error(error))) from error
        return self.action_test_connection()

    # ------------------------------------------------------------
    # S3
    # ------------------------------------------------------------

    def _s3_client(self):
        self.ensure_one()
        try:
            import boto3
            from botocore.config import Config
        except ImportError as error:
            raise UserError(_("Install the Python library boto3 on the server: pip install boto3")) from error
        config = self.sudo()
        if not (config.s3_access_key and config.s3_secret_key and config.s3_bucket):
            raise UserError(_("Enter the bucket, the access key and the secret key of %s.", self.name))
        return boto3.client(
            "s3",
            endpoint_url=(config.s3_endpoint_url or "").strip() or None,
            region_name=(config.s3_region or "").strip() or "us-east-1",
            aws_access_key_id=config.s3_access_key.strip(),
            aws_secret_access_key=config.s3_secret_key.strip(),
            verify=config.s3_verify_ssl,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": config.s3_addressing_style},
                retries={"max_attempts": 5, "mode": "standard"},
                connect_timeout=15,
                read_timeout=300,
                # Many S3-compatible storages reject the CRC checksums sent by default since botocore 1.36
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    def _s3_key_prefix(self):
        self.ensure_one()
        parts = [(self.s3_prefix or "").strip("/"), self.env.cr.dbname]
        return "/".join(p for p in parts if p)

    def _s3_check(self):
        client = self._s3_client()
        key = f"{self._s3_key_prefix()}/.vesta-backup-test"
        try:
            client.head_bucket(Bucket=self.s3_bucket)
        except Exception as error:
            if self._s3_error_code(error) in ("404", "NoSuchBucket"):
                raise UserError(
                    _(
                        "The bucket %s doesn't exist. Use the Create Bucket button or create it on the storage.",
                        self.s3_bucket,
                    )
                ) from error
            raise UserError(_("Connection failed: %s", self._s3_error(error))) from error
        try:
            client.put_object(Bucket=self.s3_bucket, Key=key, Body=b"ok")
            client.delete_object(Bucket=self.s3_bucket, Key=key)
        except Exception as error:
            raise UserError(_("Could not write to the bucket: %s", self._s3_error(error))) from error

    @staticmethod
    def _s3_error_code(error):
        response = getattr(error, "response", None) or {}
        return str(response.get("Error", {}).get("Code", ""))

    @staticmethod
    def _s3_error(error):
        response = getattr(error, "response", None) or {}
        if err := response.get("Error"):
            return f"{err.get('Code', '')} {err.get('Message', '')}".strip()
        return str(error)
