# Copyright 2026 Vesta AI (https://vestaai.ir)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Build the backup files of a database: pg_dump, filestore archive and Odoo zip."""

import hashlib
import json
import os
import subprocess
import tarfile

import odoo
from odoo.modules import db as modules_db
from odoo.tools import config
from odoo.tools.misc import exec_pg_environ, find_pg_tool

CHUNK_SIZE = 1024 * 1024


class BackupError(Exception):
    pass


def backup_root(db_name):
    """Folder of the backups kept on the server disk.

    It can only be changed in the server configuration (``vesta_backup_dir``), never from
    the user interface: downloads and deletions are confined to this folder.
    """
    root = config.get("vesta_backup_dir") or os.path.join(config["data_dir"], "backups")
    return os.path.realpath(os.path.join(root, db_name))


def is_inside(path, root):
    path = os.path.realpath(path)
    return os.path.commonpath([path, root]) == root and path != root


def dump_database(db_name, path):
    """Write a compressed pg_dump of the database (custom format, restore with pg_restore)."""
    cmd = [find_pg_tool("pg_dump"), "--no-owner", "--format=c", f"--file={path}", db_name]
    proc = subprocess.run(
        cmd,
        env=exec_pg_environ(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode:
        message = proc.stderr.decode(errors="replace").strip()[-2000:]
        raise BackupError(f"pg_dump failed ({proc.returncode}): {message}")


def archive_filestore(db_name, path, compresslevel=6):
    """Write the filestore of the database as ``filestore/`` inside a gzipped tar."""
    filestore = config.filestore(db_name)
    with tarfile.open(path, "w:gz", compresslevel=compresslevel) as tar:
        if os.path.isdir(filestore):
            tar.add(filestore, arcname="filestore")
        else:
            info = tarfile.TarInfo("filestore")
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            tar.addfile(info)


def dump_zip(db_name, path, with_filestore=True):
    """Write the standard Odoo backup zip, restorable from the database manager."""
    with open(path, "wb") as stream:
        modules_db.dump(db_name, stream, backup_format="zip", with_filestore=with_filestore)


def write_manifest(cr, path):
    manifest = modules_db._dump_db_manifest(cr)
    manifest["vesta_backup"] = {
        "database": "pg_dump custom format, restore with: pg_restore --no-owner -d <db> <file>.dump",
        "filestore": "tar.gz, extract filestore/ into <data_dir>/filestore/<db>/",
        "server_version": odoo.release.version,
    }
    with open(path, "w", encoding="utf-8") as manifest_file:
        json.dump(manifest, manifest_file, indent=4)


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()
