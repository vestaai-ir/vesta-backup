# Copyright 2026 Vesta AI (https://vestaai.ir)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
{
    "name": "Vesta Backup",
    "summary": "Scheduled backups of the database and the filestore to S3, MinIO or the server disk,"
    " with retention and one-click download",
    "version": "20.0.1.0.0",
    "category": "Administration",
    "website": "https://github.com/vestaai-ir/vesta-backup",
    "author": "Vesta AI",
    "license": "LGPL-3",
    "application": True,
    "installable": True,
    "depends": ["mail"],
    "external_dependencies": {"python": ["boto3"]},
    "data": [
        "security/ir.access.csv",
        "data/ir_cron_data.xml",
        "data/vesta_backup_config_data.xml",
        "views/vesta_backup_config_views.xml",
        "views/vesta_backup_job_views.xml",
        "views/vesta_backup_menus.xml",
    ],
    "images": ["static/description/banner.png"],
}
