- **Backup Now** on a destination queues a backup; it runs in the background within a few seconds.
- **Vesta Backup > Backups** lists every backup with its status, size and duration; click **Download** to
  save it to your computer. A separate backup has three files: database, files and manifest.
- The destination *Download to my computer* (server disk, single zip, not scheduled) is created on
  installation for quick manual backups to download.

Restore a separate backup:

```bash
createdb -O odoo newdb
pg_restore --no-owner -d newdb mydb_20261116_230000.dump
mkdir -p <data_dir>/filestore/newdb
tar xzf mydb_20261116_230000_filestore.tar.gz --strip-components=1 -C <data_dir>/filestore/newdb
```

Restore a zip backup from `/web/database/manager` (Restore Database) or with
`odoo db load newdb mydb_20261116_230000.zip`.
