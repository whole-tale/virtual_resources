#!/usr/bin/env python
# -*- coding: utf-8 -*-
import base64
import datetime
import pathlib

from girder.api.rest import Resource
from girder.constants import AccessType
from girder.exceptions import ValidationException
from girder.models.folder import Folder


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
            path, root_id = VirtualObject.path_from_id(obj_id)
        elif parent_id.startswith("wtlocal:"):
            path, root_id = VirtualObject.path_from_id(parent_id)
        elif parent_id in virtual_folders:  # root
            root_folder = Folder().load(parent_id, force=True)
            path = root_folder["fsPath"]
            root_id = str(root_folder["_id"])

        if path:
            path = pathlib.Path(path)
            if path.is_absolute():
                func(self, event, path, root_id)

    return wrapper


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
        decoded = base64.b64decode(object_id[8:]).decode()
        path, root_id = decoded.split("|")
        return pathlib.Path(path), root_id

    def is_file(self, path, root_id):
        if not path.is_file():
            raise ValidationException(
                "Invalid ObjectId: %s" % self.generate_id(path, root_id), field="id"
            )

    def is_dir(self, path, root_id):
        if not path.is_dir():
            raise ValidationException(
                "Invalid ObjectId: %s" % self.generate_id(path, root_id), field="id"
            )

    def vFolder(self, path, root_id):
        self.is_dir(path, root_id)
        stat = path.stat()
        return {
            "_id": self.generate_id(path.as_posix(), root_id),
            "_modelType": "folder",
            "_accessLevel": AccessType.ADMIN,
            "name": path.parts[-1],
            "parentId": self.generate_id(path.parent.as_posix(), root_id),
            "created": datetime.datetime.fromtimestamp(stat.st_ctime),
            "updated": datetime.datetime.fromtimestamp(stat.st_mtime),
            "size": stat.st_size,
            "public": True,
            "lowerName": path.parts[-1].lower(),
        }

    def vItem(self, path, root_id):
        self.is_file(path, root_id)
        stat = path.stat()
        return {
            "_id": self.generate_id(path.as_posix(), root_id),
            "_modelType": "item",
            "_accessLevel": AccessType.ADMIN,
            "name": path.parts[-1],
            "folderId": self.generate_id(path.parent.as_posix(), root_id),
            "created": datetime.datetime.fromtimestamp(stat.st_ctime),
            "updated": datetime.datetime.fromtimestamp(stat.st_mtime),
            "size": stat.st_size,
            "lowerName": path.parts[-1].lower(),
        }

    def _File(self, path, root_id):
        self.is_file(path, root_id)
        stat = path.stat()
        return {
            "_id": self.generate_id(path.as_posix(), root_id),
            "_modelType": "file",
            "name": path.parts[-1],
            "size": stat.st_size,
            "exts": [],
            "creatorId": "user_id",
            "created": datetime.datetime.fromtimestamp(stat.st_ctime),
            "itemId": self.generate_id(path.as_posix(), root_id),
        }
