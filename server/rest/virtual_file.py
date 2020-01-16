#!/usr/bin/env python
# -*- coding: utf-8 -*-
import cherrypy
import errno
import os
import shutil
import stat

from girder import events
from girder.api.rest import setResponseHeader
from girder.exceptions import (
    GirderException,
    AccessException,
    RestException,
)
from girder.models.assetstore import Assetstore
from girder.models.upload import Upload
from girder.utility import RequestBodyStream, assetstore_utilities

from . import VirtualObject, validate_event


BUF_SIZE = 65536
DEFAULT_PERMS = stat.S_IRUSR | stat.S_IWUSR


class VirtualFile(VirtualObject):
    def __init__(self):
        super(VirtualFile, self).__init__()
        self.resourceName = "virtual_file"
        name = "virtual_resources"

        events.bind("rest.post.file.before", name, self.create_file)
        # GET /file/:id
        events.bind("rest.put.file/:id.before", name, self.rename_file)
        events.bind("rest.delete.file/:id.before", name, self.remove_file)
        # PUT /file/:id/contents
        # POST /file/:id/copy
        events.bind("rest.get.file/:id/download.before", name, self.file_download)
        # GET /file/:id/download/:name
        # PUT /file/:id/move
        events.bind("rest.post.file/chunk.before", name, self.read_chunk)
        # POST /file/completion
        # GET /file/offset
        # DELETE /file/upload/:id

    @validate_event
    def create_file(self, event, path, root_id):
        user = self.getCurrentUser()
        params = event.info["params"]
        self.is_dir(path, root_id)

        name = params["name"]
        parent = self.vFolder(path, root_id)
        file_path = path / name
        with open(file_path, "a"):
            os.utime(file_path.as_posix())

        size = int(params["size"])
        chunk = None
        if size > 0 and cherrypy.request.headers.get("Content-Length"):
            ct = cherrypy.request.body.content_type.value
            if (
                ct not in cherrypy.request.body.processors
                and ct.split("/", 1)[0] not in cherrypy.request.body.processors
            ):
                chunk = RequestBodyStream(cherrypy.request.body)
        if chunk is not None and chunk.getSize() <= 0:
            chunk = None
        try:
            upload = Upload().createUpload(
                user=user, name=name, parentType="folder", parent=parent, size=size
            )
        except OSError as exc:
            if exc.errno == errno.EACCES:
                raise GirderException(
                    "Failed to create upload.",
                    "girder.api.v1.file.create-upload-failed",
                )
            raise

        if upload["size"] > 0:
            if chunk:
                fobj = self._handle_chunk(upload, chunk, filter=True, user=user)
                event.preventDefault().addResponse(fobj)
                return
            event.preventDefault().addResponse(upload)
        else:
            event.preventDefault().addResponse(self.vFile(file_path, root_id))

    @validate_event
    def rename_file(self, event, path, root_id):
        self.is_file(path, root_id)
        new_path = path.with_name(event.info["params"]["name"])
        path.rename(new_path)
        event.preventDefault().addResponse(self.vFile(new_path, root_id))

    @validate_event
    def remove_file(self, event, path, root_id):
        self.is_file(path, root_id)
        path.unlink()
        event.preventDefault().addResponse({"message": "Deleted file %s." % path.name})

    @validate_event
    def file_download(self, event, path, root_id):
        fobj = self.vFile(path, root_id)

        endByte = max(
            int(event.info["params"].get("endByte", fobj["size"])), fobj["size"]
        )
        offset = int(event.info["params"].get("offset", "0"))

        setResponseHeader("Content-Type", "application/octet-stream")
        setResponseHeader("Content-Length", max(endByte - offset, 0))
        if (offset or endByte < fobj["size"]) and fobj["size"]:
            setResponseHeader(
                "Content-Range", "bytes %d-%d/%d" % (offset, endByte - 1, fobj["size"])
            )
        disp = event.info["params"].get("contentDisposition", "attachment")
        if disp == "inline":
            setResponseHeader("Content-Disposition", "inline")
        else:
            setResponseHeader(
                "Content-Disposition", 'attachment; filename="%s"' % fobj["name"]
            )

        def stream():
            bytesRead = offset
            with open(path, "rb") as f:
                if offset > 0:
                    f.seek(offset)

                while True:
                    readLen = min(BUF_SIZE, endByte - bytesRead)
                    if readLen <= 0:
                        break

                    data = f.read(readLen)
                    bytesRead += readLen

                    if not data:
                        break
                    yield data

        event.preventDefault().addResponse(stream)

    def _finalize_upload(self, upload, assetstore=None):
        if assetstore is None:
            assetstore = Assetstore().load(upload["assetstoreId"])
        path, root_id = self.path_from_id(upload["parentId"])
        abspath = path / upload["name"]
        shutil.move(upload["tempFile"], abspath.as_posix())
        try:
            os.chmod(abspath, assetstore.get("perms", DEFAULT_PERMS))
        except OSError:
            pass
        return self.vFile(abspath, root_id)

    def _handle_chunk(self, upload, chunk, filter=False, user=None):
        assetstore = Assetstore().load(upload["assetstoreId"])
        adapter = assetstore_utilities.getAssetstoreAdapter(assetstore)

        upload = adapter.uploadChunk(upload, chunk)
        if "_id" in upload or upload["received"] != upload["size"]:
            upload = Upload().save(upload)

        # If upload is finished, we finalize it
        if upload["received"] == upload["size"]:
            return self._finalize_upload(upload)
        else:
            return upload

    def read_chunk(self, event):
        params = event.info["params"]
        if "chunk" in params:
            chunk = params["chunk"]
            if isinstance(chunk, cherrypy._cpreqbody.Part):
                # Seek is the only obvious way to get the length of the part
                chunk.file.seek(0, os.SEEK_END)
                size = chunk.file.tell()
                chunk.file.seek(0, os.SEEK_SET)
                chunk = RequestBodyStream(chunk.file, size=size)
        else:
            chunk = RequestBodyStream(cherrypy.request.body)

        user = self.getCurrentUser()
        offset = int(params.get("offset", 0))
        upload = Upload().load(params["uploadId"])

        if upload["userId"] != user["_id"]:
            raise AccessException("You did not initiate this upload.")

        if upload["received"] != offset:
            raise RestException(
                "Server has received %s bytes, but client sent offset %s."
                % (upload["received"], offset)
            )

        try:
            fobj = self._handle_chunk(upload, chunk, filter=True, user=user)
            event.preventDefault().addResponse(fobj)
        except IOError as exc:
            if exc.errno == errno.EACCES:
                raise Exception("Failed to store upload.")
            raise
