#!/usr/bin/env python
# -*- coding: utf-8 -*-

import base64
import cherrypy
import datetime
import errno
import json
import pathlib
from operator import itemgetter
import os
import shutil
import stat

from girder import events
from girder.api.v1.folder import Folder as FolderResource
from girder.api.rest import setResponseHeader, boundHandler
from girder.constants import AccessType
from girder.exceptions import (
    GirderException,
    ValidationException,
    AccessException,
    RestException,
)
from girder.models.assetstore import Assetstore
from girder.models.collection import Collection
from girder.models.folder import Folder
from girder.models.upload import Upload
from girder.utility import RequestBodyStream, assetstore_utilities
from girder.utility.progress import ProgressContext


BUF_SIZE = 65536
DEFAULT_PERMS = stat.S_IRUSR | stat.S_IWUSR
BASE_COLLECTION = None
BASE_FOLDER = None
LOCAL_ROOT = "/tmp"


def _generate_id(path):
    if isinstance(path, pathlib.Path):
        path = path.as_posix()
    return "wtlocal:" + base64.b64encode(path.encode()).decode()


def path_from_id(object_id):
    return pathlib.Path(base64.b64decode(object_id[8:]).decode())


def _Folder(path):
    if not path.is_dir():
        raise ValidationException(
            "Invalid ObjectId: %s" % _generate_id(path), field="id"
        )
    stat = path.stat()
    return {
        "_id": _generate_id(path.as_posix()),
        "_modelType": "folder",
        "_accessLevel": AccessType.ADMIN,
        "name": path.parts[-1],
        "parentId": _generate_id(path.parent.as_posix()),
        "created": datetime.datetime.fromtimestamp(stat.st_ctime),
        "updated": datetime.datetime.fromtimestamp(stat.st_mtime),
        "size": stat.st_size,
        "public": True,
        "lowerName": path.parts[-1].lower(),
    }


def _Item(path):
    if not path.is_file():
        raise ValidationException(
            "Invalid ObjectId: %s" % _generate_id(path), field="id"
        )
    stat = path.stat()
    return {
        "_id": _generate_id(path.as_posix()),
        "_modelType": "item",
        "_accessLevel": AccessType.ADMIN,
        "name": path.parts[-1],
        "folderId": _generate_id(path.parent.as_posix()),
        "created": datetime.datetime.fromtimestamp(stat.st_ctime),
        "updated": datetime.datetime.fromtimestamp(stat.st_mtime),
        "size": stat.st_size,
        "lowerName": path.parts[-1].lower(),
    }


def _File(path):
    if not path.is_file():
        raise ValidationException(
            "Invalid ObjectId: %s" % _generate_id(path), field="id"
        )
    stat = path.stat()
    return {
        "_id": _generate_id(path.as_posix()),
        "_modelType": "file",
        "name": path.parts[-1],
        "size": stat.st_size,
        "exts": [],
        "creatorId": "user_id",
        "created": datetime.datetime.fromtimestamp(stat.st_ctime),
        "itemId": _generate_id(path.as_posix()),
    }


def validate_event(func):
    def wrapper(self, event):
        params = event.info.get("params", {})
        obj_id = event.info.get("id", "")
        parent_id = (
            params.get("parentId", "")
            or params.get("folderId", "")
            or params.get("itemId", "")
        )

        # TODO: This might be costly. Is there a better way?
        virtual_folders = {
            str(folder["_id"])
            for folder in Folder().find({"isMapping": True}, projection={"_id"})
        }

        path = None
        if obj_id.startswith("wtlocal:"):
            path = base64.b64decode(obj_id[8:]).decode()
        elif parent_id.startswith("wtlocal:"):
            path = base64.b64decode(parent_id[8:]).decode()
        elif parent_id in virtual_folders:  # root
            root_folder = Folder().load(parent_id, force=True)
            path = root_folder["fsPath"]

        if path:
            path = pathlib.Path(path)
            if path.is_absolute():
                func(self, event, path)

    return wrapper


@boundHandler
@validate_event
def get_folder_info(self, event, path):
    event.preventDefault().addResponse(_Folder(path))


@boundHandler
@validate_event
def get_item_info(self, event, path):
    event.preventDefault().addResponse(_Item(path))


@boundHandler
@validate_event
def get_child_items(self, event, path):
    response = [_Item(obj) for obj in path.iterdir() if obj.is_file()]
    event.preventDefault().addResponse(sorted(response, key=itemgetter("name")))


@boundHandler
@validate_event
def get_child_folders(self, event, path):
    response = [_Folder(obj) for obj in path.iterdir() if obj.is_dir()]
    event.preventDefault().addResponse(sorted(response, key=itemgetter("name")))


@boundHandler
@validate_event
def folder_root_path(self, event, path):
    response = [dict(type="folder", object=_Folder(path))]
    path = path.parent
    while path != pathlib.Path("/"):
        response.append(dict(type="folder", object=_Folder(path)))
        path = path.parent

    response.append(dict(type="collection", object=BASE_COLLECTION))
    response.pop(0)
    event.preventDefault().addResponse(response[::-1])


@boundHandler
@validate_event
def item_root_path(self, event, path):
    response = [dict(type="item", object=_Item(path))]
    path = path.parent
    while path != pathlib.Path("/"):
        response.append(dict(type="folder", object=_Folder(path)))
        path = path.parent

    response.append(dict(type="collection", object=BASE_COLLECTION))
    response.pop(0)
    event.preventDefault().addResponse(response[::-1])


@boundHandler
@validate_event
def get_child_files(self, event, path):
    event.preventDefault().addResponse([_File(path)])


@boundHandler
@validate_event
def get_folder_details(self, event, path):
    if not (path.exists() and path.is_dir()):
        raise ValidationException(
            "Invalid ObjectId: %s" % _generate_id(path), field="id"
        )

    response = dict(nFolders=0, nItems=0)
    for obj in path.iterdir():
        if obj.is_dir():
            response["nFolders"] += 1
        elif obj.is_file():
            response["nItems"] += 1
    event.preventDefault().addResponse(response)


@boundHandler
@validate_event
def file_download(self, event, path):
    if not (path.exists() and path.is_file()):
        raise ValidationException(
            "Invalid ObjectId: %s" % _generate_id(path), field="id"
        )

    fobj = _File(path)

    endByte = max(int(event.info["params"].get("endByte", fobj["size"])), fobj["size"])
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


@boundHandler
@validate_event
def create_folder(self, event, path):
    params = event.info["params"]
    new_path = path / params["name"]
    new_path.mkdir()
    event.preventDefault().addResponse(_Folder(new_path))


@boundHandler
@validate_event
def create_item(self, event, path):
    params = event.info["params"]
    new_path = path / params["name"]
    with open(new_path, "a"):
        os.utime(new_path.as_posix())
    event.preventDefault().addResponse(_Item(new_path))


@boundHandler
@validate_event
def rename_item(self, event, path):
    if not (path.exists() and path.is_file()):
        raise ValidationException(
            "Invalid ObjectId: %s" % _generate_id(path), field="id"
        )

    new_path = path.with_name(event.info["params"]["name"])
    path.rename(new_path)
    event.preventDefault().addResponse(_Item(new_path))


@boundHandler
@validate_event
def rename_file(self, event, path):
    if not (path.exists() and path.is_file()):
        raise ValidationException(
            "Invalid ObjectId: %s" % _generate_id(path), field="id"
        )

    new_path = path.with_name(event.info["params"]["name"])
    path.rename(new_path)
    event.preventDefault().addResponse(_File(new_path))


@boundHandler
@validate_event
def rename_folder(self, event, path):
    if not (path.exists() and path.is_dir()):
        raise ValidationException(
            "Invalid ObjectId: %s" % _generate_id(path), field="id"
        )

    new_path = path.with_name(event.info["params"]["name"])
    path.rename(new_path)
    event.preventDefault().addResponse(_Folder(new_path))


@boundHandler
@validate_event
def remove_item(self, event, path):
    if not (path.exists() and path.is_file()):
        raise ValidationException(
            "Invalid ObjectId: %s" % _generate_id(path), field="id"
        )

    path.unlink()
    event.preventDefault().addResponse({"message": "Deleted item %s." % path.name})


@boundHandler
@validate_event
def remove_file(self, event, path):
    if not (path.exists() and path.is_file()):
        raise ValidationException(
            "Invalid ObjectId: %s" % _generate_id(path), field="id"
        )

    path.unlink()
    event.preventDefault().addResponse({"message": "Deleted file %s." % path.name})


@boundHandler
@validate_event
def copy_item(self, event, path):
    # TODO: folderId is not passed properly, but that's vanilla girder's fault...
    if not (path.exists() and path.is_file()):
        raise ValidationException(
            "Invalid ObjectId: %s" % _generate_id(path), field="id"
        )

    name = event.info["params"].get("name") or path.name
    new_path = path_from_id(event.info["params"]["folderId"]) / name
    shutil.copy(path.as_posix(), new_path.as_posix())
    event.preventDefault().addResponse(_Item(new_path))


def _finalize_upload(upload, assetstore=None):
    if assetstore is None:
        assetstore = Assetstore().load(upload["assetstoreId"])
    abspath = path_from_id(upload["parentId"]) / upload["name"]
    shutil.move(upload["tempFile"], abspath.as_posix())
    try:
        os.chmod(abspath, assetstore.get("perms", DEFAULT_PERMS))
    except OSError:
        pass
    return _File(abspath)


def _handle_chunk(upload, chunk, filter=False, user=None):
    assetstore = Assetstore().load(upload["assetstoreId"])
    adapter = assetstore_utilities.getAssetstoreAdapter(assetstore)

    upload = adapter.uploadChunk(upload, chunk)
    if "_id" in upload or upload["received"] != upload["size"]:
        upload = Upload().save(upload)

    # If upload is finished, we finalize it
    if upload["received"] == upload["size"]:
        return _finalize_upload(upload)
    else:
        return upload


@boundHandler
@validate_event
def create_file(self, event, path):
    user = self.getCurrentUser()
    params = event.info["params"]
    if not (path.exists() and path.is_dir()):
        raise ValidationException(
            "Invalid Folder Id in create_file: {}".format(_generate_id(path)),
            field="id",
        )

    name = params["name"]
    parent = _Folder(path)
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
                "Failed to create upload.", "girder.api.v1.file.create-upload-failed"
            )
        raise

    if upload["size"] > 0:
        if chunk:
            fobj = _handle_chunk(upload, chunk, filter=True, user=user)
            event.preventDefault().addResponse(fobj)
            return
        event.preventDefault().addResponse(upload)
    else:
        event.preventDefault().addResponse(_File(file_path))


@boundHandler
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
        fobj = _handle_chunk(upload, chunk, filter=True, user=user)
        event.preventDefault().addResponse(fobj)
    except IOError as exc:
        if exc.errno == errno.EACCES:
            raise Exception("Failed to store upload.")
        raise


@boundHandler
@validate_event
def move_resources(self, event, path):
    user = self.getCurrentUser()
    resources = json.loads(event.info["params"]["resources"])
    progress = event.info["params"]["progress"]
    total = sum([len(resources[key]) for key in resources])
    with ProgressContext(
        progress,
        user=user,
        title="Moving resources",
        message="Calculating requirements...",
        total=total,
    ) as ctx:
        for kind in resources:
            for obj_id in resources[kind]:
                source_path = path_from_id(obj_id)
                ctx.update(message="Moving %s %s" % (kind, source_path.name))
                shutil.move(
                    source_path.as_posix(), (path / source_path.name).as_posix()
                )
                ctx.update(increment=1)
    event.preventDefault().addResponse(None)


@boundHandler
@validate_event
def copy_resources(self, event, path):
    user = self.getCurrentUser()
    resources = json.loads(event.info["params"]["resources"])
    progress = event.info["params"]["progress"]
    total = sum([len(resources[key]) for key in resources])
    with ProgressContext(
        progress,
        user=user,
        title="Copying resources",
        message="Calculating requirements...",
        total=total,
    ) as ctx:
        for kind in resources:
            for obj_id in resources[kind]:
                source_path = path_from_id(obj_id)
                ctx.update(message="Copying %s %s" % (kind, source_path.name))
                if kind == "folder":
                    shutil.copytree(
                        source_path.as_posix(), (path / source_path.name).as_posix()
                    )
                else:
                    shutil.copy(
                        source_path.as_posix(), (path / source_path.name).as_posix()
                    )
                ctx.update(increment=1)
    event.preventDefault().addResponse(None)


@boundHandler
def delete_resources(self, event):
    user = self.getCurrentUser()
    resources = json.loads(event.info["params"]["resources"])
    remaining_resources = dict(folder=[], item=[])
    wt_resources = dict(folder=[], item=[])
    for kind in resources:
        for obj_id in resources[kind]:
            if obj_id.startswith("wtlocal:"):
                wt_resources[kind].append(obj_id)
            else:
                remaining_resources[kind].append(obj_id)

    progress = event.info["params"]["progress"]
    total = sum([len(wt_resources[key]) for key in wt_resources])
    with ProgressContext(
        progress,
        user=user,
        title="Deleting resources",
        message="Calculating requirements...",
        total=total,
    ) as ctx:
        for kind in wt_resources:
            for obj_id in wt_resources[kind]:
                source_path = path_from_id(obj_id)
                ctx.update(message="Deleting %s %s" % (kind, source_path.name))
                if kind == "folder":
                    shutil.rmtree(source_path.as_posix())
                else:
                    source_path.unlink()
                ctx.update(increment=1)
    total = sum([len(remaining_resources[key]) for key in remaining_resources])
    event.info["params"]["resources"] = json.dumps(remaining_resources)
    if total == 0:
        event.preventDefault().addResponse(None)


def _validateFolder(event):
    doc = event.info

    if "isMapping" in doc and not isinstance(doc["isMapping"], bool):
        raise ValidationException(
            "The isMapping field must be boolean.", field="isMapping"
        )

    if doc.get("isMapping"):
        # Make sure it doesn't have children
        if list(Folder().childItems(doc, limit=1)):
            raise ValidationException(
                "Virtual folders may not contain child items.", field="isMapping"
            )
        if list(
            Folder().find(
                {"parentId": doc["_id"], "parentCollection": "folder"}, limit=1
            )
        ):
            raise ValidationException(
                "Virtual folders may not contain child folders.", field="isMapping"
            )
    if doc["parentCollection"] == "folder":
        parent = Folder().load(event.info["parentId"], force=True, exc=True)
        if parent.get("isMapping"):
            raise ValidationException(
                "You may not place folders under a virtual folder.", field="folderId"
            )


@boundHandler
def mapping_folder_update(self, event):
    params = event.info["params"]
    if {"isMapping", "fsPath"} & set(params):
        folder = Folder().load(event.info["returnVal"]["_id"], force=True)
        update = False

        if params.get("isMapping") is not None:
            update = True
            folder["isMapping"] = params["isMapping"]
        if params.get("fsPath") is not None:
            update = True
            folder["fsPath"] = params["fsPath"]

        if update:
            self.requireAdmin(
                self.getCurrentUser(), "Must be admin to setup virtual folders."
            )
            folder = Folder().filter(Folder().save(folder), self.getCurrentUser())
            event.preventDefault().addResponse(folder)


def load(info):
    global BASE_COLLECTION
    BASE_COLLECTION = Collection().createCollection(
        "Local tmp", public=True, reuseExisting=True
    )
    base_folder = Folder().createFolder(
        BASE_COLLECTION, "tmp", parentType="collection", public=True, reuseExisting=True
    )
    base_folder["isMapping"] = True
    base_folder["fsPath"] = "/tmp"
    Folder().save(base_folder)

    base_folder = Folder().createFolder(
        BASE_COLLECTION,
        "home",
        parentType="collection",
        public=True,
        reuseExisting=True,
    )
    base_folder["isMapping"] = True
    base_folder["fsPath"] = "/home"
    Folder().save(base_folder)

    events.bind("rest.get.item.before", info["name"], get_child_items)
    events.bind("rest.post.item.before", info["name"], create_item)
    events.bind("rest.get.item/:id.before", info["name"], get_item_info)
    events.bind("rest.put.item/:id.before", info["name"], rename_item)
    events.bind("rest.delete.item/:id.before", info["name"], remove_item)
    events.bind("rest.post.item/:id/copy.before", info["name"], copy_item)
    events.bind("rest.get.item/:id/download.before", info["name"], file_download)
    events.bind("rest.get.item/:id/files.before", info["name"], get_child_files)
    # PUT/DELETE /item/:id/metadata
    events.bind("rest.get.item/:id/rootpath.before", info["name"], item_root_path)

    events.bind("rest.post.file.before", info["name"], create_file)
    # GET /file/:id
    events.bind("rest.put.file/:id.before", info["name"], rename_file)
    events.bind("rest.delete.file/:id.before", info["name"], remove_file)
    # PUT /file/:id/contents
    # POST /file/:id/copy
    events.bind("rest.get.file/:id/download.before", info["name"], file_download)
    # GET /file/:id/download/:name
    # PUT /file/:id/move
    events.bind("rest.post.file/chunk.before", info["name"], read_chunk)
    # POST /file/completion
    # GET /file/offset
    # DELETE /file/upload/:id

    events.bind("rest.get.folder.before", info["name"], get_child_folders)
    events.bind("rest.post.folder.before", info["name"], create_folder)
    events.bind("rest.get.folder/:id.before", info["name"], get_folder_info)
    events.bind("rest.put.folder/:id.before", info["name"], rename_folder)
    # DELETE /folder/:id
    # GET /folder/:id/access
    # PUT /folder/:id/access
    # PUT /folder/:id/check
    # DELETE /folder/:id/contents
    # POST /folder/:id/copy
    events.bind("rest.get.folder/:id/details.before", info["name"], get_folder_details)
    # GET /folder/:id/download
    # PUT/DELETE /folder/:id/metadata
    events.bind("rest.get.folder/:id/rootpath.before", info["name"], folder_root_path)

    events.bind("rest.delete.resource.before", info["name"], delete_resources)
    # GET /resource/:id
    # GET /resource/:id/path
    # PUT /resource/:id/timestamp
    events.bind("rest.post.resource/copy.before", info["name"], copy_resources)
    # GET /resource/:id/download
    # GET /resource/lookup
    events.bind("rest.put.resource/move.before", info["name"], move_resources)
    # GET /resource/search

    events.bind("rest.post.folder.after", info["name"], mapping_folder_update)
    events.bind("rest.put.folder/:id.after", info["name"], mapping_folder_update)

    Folder().exposeFields(level=AccessType.READ, fields={"isMapping"})
    Folder().exposeFields(level=AccessType.SITE_ADMIN, fields={"fsPath"})
    for endpoint in (FolderResource.updateFolder, FolderResource.createFolder):
        (
            endpoint.description.param(
                "isMapping",
                "Whether this is a virtual folder.",
                required=False,
                dataType="boolean",
            ).param("fsPath", "Local filesystem path it maps to.", required=False)
        )
