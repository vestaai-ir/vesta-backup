# Copyright 2026 Vesta AI (https://vestaai.ir)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).

import mimetypes
import os

from werkzeug.exceptions import Forbidden, NotFound

from odoo import http
from odoo.http import Response, request
from odoo.http.stream import Stream, content_disposition

CHUNK_SIZE = 1024 * 1024


class VestaBackupController(http.Controller):
    @http.route("/vesta_backup/download/<int:file_id>", type="http", auth="user", methods=["GET"])
    def download(self, file_id):
        if not request.env.user._is_system():
            raise Forbidden()
        file = request.env["vesta.backup.file"].browse(file_id).exists()
        if not file or file.job_id.state != "done":
            raise NotFound()
        mimetype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"

        if file.job_id.destination == "local":
            path = file._local_path()
            if not path or not os.path.isfile(path):
                raise NotFound()
            stat = os.stat(path)
            stream = Stream(
                type="path",
                path=path,
                mimetype=mimetype,
                download_name=file.name,
                etag=f"{int(stat.st_mtime)}-{stat.st_size}",
                last_modified=stat.st_mtime,
                size=stat.st_size,
            )
            return stream.get_response(as_attachment=True)

        config = file.config_id
        client = config._s3_client()
        if not config.s3_download_proxy:
            url = client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": config.s3_bucket,
                    "Key": file.location,
                    "ResponseContentDisposition": content_disposition(file.name),
                },
                ExpiresIn=3600,
            )
            return request.redirect(url, local=False)
        obj = client.get_object(Bucket=config.s3_bucket, Key=file.location)
        headers = [
            ("Content-Type", mimetype),
            ("Content-Disposition", content_disposition(file.name)),
            ("Content-Length", str(obj["ContentLength"])),
            ("Cache-Control", "no-store"),
        ]
        return Response(obj["Body"].iter_chunks(CHUNK_SIZE), headers=headers, direct_passthrough=True)
