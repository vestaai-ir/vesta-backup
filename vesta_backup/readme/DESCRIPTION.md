Vesta Backup backs up the current database and its filestore on a schedule, to any S3-compatible storage
(Amazon S3, a self-hosted MinIO, ArvanCloud, Cloudflare R2, Wasabi, ...) or to the disk of the server, and
lets administrators download any backup to their own computer.

- Two formats: the database (compressed `pg_dump`, `.dump`) and the files (`.tar.gz`) as **separate** files
  with a manifest, or a **single zip** in the format of the Odoo database manager.
- Several destinations, each with its own schedule (every N hours, days, weeks or months) and retention
  (keep the last N backups and/or N days; the latest backup is never deleted).
- Backups run in the background; multipart uploads for large files; SHA-256 checksum of every file.
- One-click download, streamed through Odoo or with a temporary signed link of the storage.
- Failures are posted on the destination, so its followers get an email.
