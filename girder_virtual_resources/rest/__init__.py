#!/usr/bin/env python
# -*- coding: utf-8 -*-
import base64
import copy
import datetime
import os
import pathlib

from girder.api.rest import Resource
from girder.constants import AccessType
from girder.exceptions import ValidationException
from girder.models.folder import Folder
from girder.models.upload import Upload


def bail_if_exists(path):
    if path.exists():
        raise ValidationException(
            "A folder or file with that name already exists here.", "name"
        )


def ensure_unique_path(dirname, name):
    checkName = (dirname / name).exists()
    new_name = name
    n = 0
    while checkName:
        n += 1
        new_name = "%s (%d)" % (name, n)
        checkName = (dirname / new_name).exists()
    return dirname / new_name


def validate_event(level=AccessType.READ, validate_admin=False):
    def validation(func):
        def wrapper(self, event):
            params = event.info.get("params", {})
            if {"isMapping", "fsPath"} & set(params) and validate_admin:
                folder = Folder().load(event.info["id"], force=True)
                update = False

                if params.get("isMapping") is not None:
                    update = True
                    folder["isMapping"] = bool(params["isMapping"])
                if params.get("fsPath") is not None:
                    update = True
                    folder["fsPath"] = params["fsPath"]

                if update:
                    self.requireAdmin(
                        self.getCurrentUser(), "Must be admin to setup virtual folders."
                    )
                    folder = Folder().filter(Folder().save(folder), self.getCurrentUser())
                    pathlib.Path(folder["fsPath"]).mkdir(
                        mode=0o755, parents=True, exist_ok=True
                    )
                    event.preventDefault().addResponse(folder)
                    return

            if "uploadId" in params:
                upload = Upload().load(params["uploadId"])
                try:
                    parent_id = str(upload["parentId"])
                    parent_type = upload["parentType"]
                except KeyError:
                    parent_id = str(upload["fileId"])
                    parent_type = None
            else:
                parent_id = params.get("parentId")
                parent_type = params.get("parentType") or "folder"

            obj_id = event.info.get("id")
            folder_id = params.get("folderId")
            item_id = params.get("itemId")
            any_parent_id = parent_id or folder_id or item_id

            path = None
            if obj_id:
                path, root_id = VirtualObject.path_from_id(obj_id)
            elif any_parent_id and any_parent_id.startswith("wtlocal:"):
                path, root_id = VirtualObject.path_from_id(any_parent_id)
            elif (parent_id and parent_type == "folder") or folder_id:
                path, root_id = VirtualObject.path_from_id(parent_id or folder_id)

            if path:
                path = pathlib.Path(path)
                if path.is_absolute():
                    user = self.getCurrentUser()
                    root = Folder().load(root_id, level=level, user=user, exc=True)
                    func(self, event, path, root, user=user)

        return wrapper

    return validation


class VirtualObject(Resource):
    def __init__(self):
        super(VirtualObject, self).__init__()

    @staticmethod
    def generate_id(path, root_id):
        if isinstance(path, pathlib.Path):
            path = path.as_posix()
        path += "|" + str(root_id)
        return "wtlocal:" + base64.b64encode(path.encode()).decode()

    @staticmethod
    def path_from_id(object_id):
        if str(object_id).startswith("wtlocal:"):
            decoded = base64.b64decode(object_id[8:]).decode()
            path, root_id = decoded.split("|")
        else:
            root_folder = Folder().load(object_id, force=True) or {}
            path = root_folder.get("fsPath")  # only exists on virtual folders
            root_id = str(root_folder.get("_id"))
        if path:
            path = pathlib.Path(path)
        return path, root_id

    def is_file(self, path, root_id):
        if not path.is_file():
            raise ValidationException(
                "Invalid ObjectId: %s" % self.generate_id(path, root_id), field="id"
            )

    def is_symlink(self, path, root_id):
        if not path.is_symlink():
            raise ValidationException(
                "Invalid ObjectId: %s" % self.generate_id(path, root_id), field="id"
            )

    def is_dir(self, path, root_id):
        if not path.is_dir():
            raise ValidationException(
                "Invalid ObjectId: %s" % self.generate_id(path, root_id), field="id"
            )

    def vFolder(self, path, root):
        self.is_dir(path, root["_id"])
        stat = path.stat()

        if path == pathlib.Path(root["fsPath"]):
            # We want actual mtime/ctime from disk
            root.update({
                "created": datetime.datetime.fromtimestamp(stat.st_ctime),
                "updated": datetime.datetime.fromtimestamp(stat.st_mtime),
            })
            return root

        if path.parent == pathlib.Path(root["fsPath"]):
            parentId = root["_id"]
        else:
            parentId = self.generate_id(path.parent.as_posix(), root["_id"])

        return {
            "_id": self.generate_id(path.as_posix(), root["_id"]),
            "_modelType": "folder",
            "access": copy.deepcopy(root.get("access", {"users": [], "groups": []})),
            "name": path.parts[-1],
            "parentId": parentId,
            "parentCollection": "folder",
            "creatorId": None,
            "created": datetime.datetime.fromtimestamp(stat.st_ctime),
            "updated": datetime.datetime.fromtimestamp(stat.st_mtime),
            "size": stat.st_size,
            "public": root.get("public", False),
            "lowerName": path.parts[-1].lower(),
        }

    def vItem(self, path, root):
        self.is_file(path, root["_id"])
        stat = path.stat()
        return {
            "_id": self.generate_id(path.as_posix(), root["_id"]),
            "_modelType": "item",
            "name": path.parts[-1],
            "folderId": self.generate_id(path.parent.as_posix(), root["_id"]),
            "creatorId": None,
            "created": datetime.datetime.fromtimestamp(stat.st_ctime),
            "updated": datetime.datetime.fromtimestamp(stat.st_mtime),
            "size": stat.st_size,
            "lowerName": path.parts[-1].lower(),
        }

    def vLink(self, path, root):
        self.is_symlink(path, root["_id"])
        stat = path.lstat()
        return {
            "_id": self.generate_id(path.as_posix(), root["_id"]),
            "_modelType": None,
            "name": path.parts[-1],
            "folderId": self.generate_id(path.parent.as_posix(), root["_id"]),
            "creatorId": None,
            "created": datetime.datetime.fromtimestamp(stat.st_ctime),
            "updated": datetime.datetime.fromtimestamp(stat.st_mtime),
            "size": stat.st_size,
            "lowerName": path.parts[-1].lower(),
            "linkTarget": os.readlink(path)
        }

    def vFile(self, path, root):
        self.is_file(path, root["_id"])
        stat = path.stat()
        return {
            "_id": self.generate_id(path.as_posix(), root["_id"]),
            "_modelType": "file",
            "assetstoreId": None,
            "creatorId": root["creatorId"],
            "mimeType": "application/octet-stream",
            "name": path.parts[-1],
            "size": stat.st_size,
            "exts": [_[1:] for _ in path.suffixes],
            "created": datetime.datetime.fromtimestamp(stat.st_ctime),
            "updated": datetime.datetime.fromtimestamp(stat.st_mtime),
            "itemId": self.generate_id(path.as_posix(), root["_id"]),
        }
