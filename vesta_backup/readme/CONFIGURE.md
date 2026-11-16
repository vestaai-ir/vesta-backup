1. Install the Python library `boto3` on the server (`pip install boto3`), and make sure the `pg_dump`
   of the PostgreSQL client matches the version of the server (or set `pg_path` in the Odoo configuration).
2. Open **Vesta Backup > Destinations** and create a destination:
   - **S3 / MinIO**: endpoint URL (empty for Amazon S3, for example `http://minio:9000` for MinIO),
     region, bucket, folder, access key and secret key. Use the *Path* addressing style for MinIO.
     Click **Test Connection**, or **Create Bucket** if the bucket doesn't exist yet.
   - **Server Disk**: backups are kept in `<data_dir>/backups/<database>/`. To use another folder
     (for example a mounted NAS), set `vesta_backup_dir = /path` in the server configuration file; it can't
     be changed from the user interface on purpose.
3. Set the schedule and the retention. The scheduled action *Vesta Backup: run scheduled and queued
   backups* must stay active, and the server needs at least one cron thread (`max_cron_threads >= 1`).
