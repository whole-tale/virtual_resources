#!/usr/bin/env python
# -*- coding: utf-8 -*-
import errno
import os
import pathlib
import shutil
import stat

import cherrypy

from girder import events
from girder.api import access
from girder.api.rest import setResponseHeader
from girder.constants import AccessType, TokenScope
from girder.exceptions import AccessException, GirderException, RestException
from girder.models.assetstore import Assetstore
from girder.models.file import File
from girder.models.folder import Folder
from girder.models.upload import Upload
from girder.utility import RequestBodyStream, assetstore_utilities

from . import VirtualObject, bail_if_exists, validate_event

BUF_SIZE = 65536
DEFAULT_PERMS = stat.S_IRUSR | stat.S_IWUSR


class VirtualFile(VirtualObject):
    def __init__(self):
        super(VirtualFile, self).__init__()
        self.resourceName = "virtual_file"
        name = "virtual_resources"

        events.bind("rest.post.file.before", name, self.create_file)
        events.bind("rest.get.file/:id.before", name, self.get_file_info)
        events.bind("rest.put.file/:id.before", name, self.rename_file)
        events.bind("rest.delete.file/:id.before", name, self.remove_file)
        # PUT /file/:id/contents
        # POST /file/:id/copy
        events.bind("rest.get.item/:id/download.before", name, self.file_download)
        events.bind("rest.get.file/:id/download.before", name, self.file_download)
        # GET /file/:id/download/:name
        # PUT /file/:id/move
        events.bind("rest.post.file/chunk.before", name, self.read_chunk)
        # POST /file/completion
        # GET /file/offset
        # DELETE /file/upload/:id

    @access.user(scope=TokenScope.DATA_WRITE)
    @validate_event(level=AccessType.WRITE)
    def create_file(self, event, path, root, user=None):
        params = event.info["params"]
        name = params["name"]
        file_path = path / name
        try:
            path.mkdir(parents=True, exist_ok=True)
            with file_path.open(mode="a"):
                os.utime(file_path.as_posix())
        except PermissionError:
            raise GirderException(
                "Insufficient perms to write on {}".format(path.as_posix()),
                "girder.api.v1.file.create-upload-failed",
            )
        except Exception:
            raise

        parent = Folder().filter(self.vFolder(path, root), user=user)
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
        upload = Upload().createUpload(
            user=user, name=name, parentType="folder", parent=parent, size=size
        )

        if upload["size"] > 0:
            if chunk:
                fobj = self._handle_chunk(upload, chunk, filter=True, user=user)
                event.preventDefault().addResponse(fobj)
                return
            event.preventDefault().addResponse(upload)
        else:
            event.preventDefault().addResponse(
                File().filter(self.vFile(file_path, root), user=user)
            )

    @access.public(scope=TokenScope.DATA_READ)
    @validate_event(level=AccessType.READ)
    def get_file_info(self, event, path, root, user=None):
        event.preventDefault().addResponse(
            File().filter(self.vFile(path, root), user=user)
        )

    @access.user(scope=TokenScope.DATA_WRITE)
    @validate_event(level=AccessType.WRITE)
    def rename_file(self, event, path, root, user=None):
        self.is_file(path, root["_id"])
        new_path = path.with_name(event.info["params"]["name"])
        bail_if_exists(new_path)
        path.rename(new_path)
        event.preventDefault().addResponse(
            File().filter(self.vFile(new_path, root), user=user)
        )

    @access.user(scope=TokenScope.DATA_WRITE)
    @validate_event(level=AccessType.WRITE)
    def remove_file(self, event, path, root, user=None):
        self.is_file(path, root["_id"])
        path.unlink()
        event.preventDefault().addResponse({"message": "Deleted file %s." % path.name})

    @access.public(scope=TokenScope.DATA_READ, cookie=True)
    @validate_event(level=AccessType.READ)
    def file_download(self, event, path, root, user=None):
        fobj = self.vFile(path, root)

        rangeHeader = cherrypy.lib.httputil.get_ranges(
            cherrypy.request.headers.get("Range"), fobj.get("size", 0)
        )
        # The HTTP Range header takes precedence over query params
        if rangeHeader and len(rangeHeader):
            # Currently we only support a single range.
            offset, endByte = rangeHeader[0]
        else:
            endByte = min(
                int(event.info["params"].get("endByte", fobj["size"])), fobj["size"]
            )
            offset = int(event.info["params"].get("offset", "0"))

        setResponseHeader("Accept-Ranges", "bytes")
        setResponseHeader("Content-Type", "application/octet-stream")
        setResponseHeader("Content-Length", max(endByte - offset, 0))
        if (offset or endByte < fobj["size"]) and fobj["size"]:
            setResponseHeader(
                "Content-Range", "bytes %d-%d/%d" % (offset, endByte - 1, fobj["size"])
            )
        disp = event.info["params"].get("contentDisposition", "attachment")
        setResponseHeader(
            "Content-Disposition", '{}; filename="{}"'.format(disp, fobj["name"])
        )

        def stream():
            bytesRead = offset
            with path.open(mode="rb") as f:
                if offset > 0:
                    f.seek(offset)

                while True:
                    readLen = min(BUF_SIZE, endByte - bytesRead)
                    if readLen <= 0:
                        break

                    data = f.read(readLen)
                    bytesRead += readLen

                    # if not data:
                    #    break
                    yield data

        event.preventDefault().addResponse(stream)

    def _finalize_upload(self, upload, assetstore=None):
        if assetstore is None:
            assetstore = Assetstore().load(upload["assetstoreId"])
        if str(upload["parentId"]).startswith("wtlocal:"):
            path, root_id = self.path_from_id(upload["parentId"])
            root = Folder().load(root_id, force=True)  # TODO make it obsolete
        else:
            root = Folder().load(upload["parentId"], force=True)
            path = pathlib.Path(root["fsPath"])
        abspath = path / upload["name"]
        shutil.move(upload["tempFile"], abspath.as_posix())
        abspath.chmod(assetstore.get("perms", DEFAULT_PERMS))
        return self.vFile(abspath, root)

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

    @access.user(scope=TokenScope.DATA_WRITE)
    @validate_event(level=AccessType.WRITE)
    def read_chunk(self, event, path, root, user=None):
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

        if not user:
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
