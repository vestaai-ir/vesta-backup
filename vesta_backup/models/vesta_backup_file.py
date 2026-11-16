# Copyright 2026 Vesta AI (https://vestaai.ir)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).

import os

from odoo import api, fields, models
from odoo.addons.vesta_backup import archive
from odoo.tools.misc import human_size


class VestaBackupFile(models.Model):
    _name = "vesta.backup.file"
    _description = "Backup File"
    _order = "job_id desc, id"

    job_id = fields.Many2one(
        string="Backup", comodel_name="vesta.backup.job", required=True, ondelete="cascade", index=True
    )
    config_id = fields.Many2one(related="job_id.config_id", store=True)
    kind = fields.Selection(
        selection=[
            ("database", "Database"),
            ("filestore", "Files"),
            ("zip", "Database and Files (zip)"),
            ("manifest", "Manifest"),
        ],
        required=True,
    )
    name = fields.Char(string="File Name", required=True)
    location = fields.Char(readonly=True, help="Object key on S3, or path on the server disk.")
    size = fields.Float(string="Size (bytes)")
    size_display = fields.Char(string="Size", compute="_compute_size_display")
    checksum = fields.Char(string="SHA-256", readonly=True)

    @api.depends("size")
    def _compute_size_display(self):
        for file in self:
            file.size_display = self._human_size(file.size)

    @api.model
    def _human_size(self, size):
        return human_size(size) if size else "0"

    def _local_path(self):
        """Path of the file on the server disk, only if it is inside the backup folder."""
        self.ensure_one()
        if self.job_id.destination != "local" or not self.location:
            return False
        path = os.path.realpath(self.location)
        return path if archive.is_inside(path, self.env["vesta.backup.job"]._local_root()) else False

    def action_download(self):
        self.ensure_one()
        return {"type": "ir.actions.act_url", "url": f"/vesta_backup/download/{self.id}", "target": "self"}
