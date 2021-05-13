#!/usr/bin/env python
# -*- coding: utf-8 -*-
from operator import itemgetter
import os
import pathlib
import pymongo
import shutil

from girder import events
from girder.api import access
from girder.constants import TokenScope, AccessType
from girder.exceptions import GirderException, ValidationException
from girder.models.file import File
from girder.models.folder import Folder
from girder.models.item import Item

from . import VirtualObject, validate_event, ensure_unique_path


class VirtualItem(VirtualObject):
    def __init__(self):
        super(VirtualItem, self).__init__()
        self.resourceName = "virtual_item"
        name = "virtual_resources"

        events.bind("rest.get.item.before", name, self.get_child_items)
        events.bind("rest.post.item.before", name, self.create_item)
        events.bind("rest.get.item/:id.before", name, self.get_item_info)
        events.bind("rest.put.item/:id.before", name, self.rename_item)
        events.bind("rest.delete.item/:id.before", name, self.remove_item)
        events.bind("rest.post.item/:id/copy.before", name, self.copy_item)
        # events.bind("rest.get.item/:id/download.before", name, self.file_download)  # in Vfile
        events.bind("rest.get.item/:id/files.before", name, self.get_child_files)
        # PUT/DELETE /item/:id/metadata
        events.bind("rest.get.item/:id/rootpath.before", name, self.item_root_path)

    @access.public(scope=TokenScope.DATA_READ)
    @validate_event(level=AccessType.READ)
    def get_child_items(self, event, path, root, user=None):
        params = event.info["params"]
        offset = int(params.get("offset", 0))
        limit = int(params.get("limit", 50))
        sort_key = params.get("sort", "lowerName")
        reverse = int(params.get("sortdir", pymongo.ASCENDING)) == pymongo.DESCENDING

        items = [self.vItem(obj, root) for obj in path.iterdir() if obj.is_file()]
        items = sorted(items, key=itemgetter(sort_key), reverse=reverse)
        upper_bound = limit + offset if limit > 0 else None
        response = [
            Item().filter(item, user=user) for item in items[offset:upper_bound]
        ]
        event.preventDefault().addResponse(response)

    @access.user(scope=TokenScope.DATA_WRITE)
    @validate_event(level=AccessType.WRITE)
    def create_item(self, event, path, root, user=None):
        params = event.info["params"]
        new_path = path / params["name"]
        try:
            with new_path.open(mode="x"):
                os.utime(new_path.as_posix())
        except FileExistsError:
            raise ValidationException(
                "An item with that name already exists here.", "name"
            )
        event.preventDefault().addResponse(
            Item().filter(self.vItem(new_path, root), user=user)
        )

    @access.public(scope=TokenScope.DATA_READ)
    @validate_event(level=AccessType.READ)
    def get_item_info(self, event, path, root, user=None):
        event.preventDefault().addResponse(
            Item().filter(self.vItem(path, root), user=user)
        )

    @access.user(scope=TokenScope.DATA_WRITE)
    @validate_event(level=AccessType.WRITE)
    def rename_item(self, event, path, root, user=None):
        self.is_file(path, root["_id"])
        source = self.vItem(path, root)

        params = event.info.get("params", {})
        name = params.get("name", path.name)
        parentId = params.get("folderId", source["folderId"])

        if parentId == source["folderId"]:
            if path.name == name:
                new_path = path
            else:
                new_path = path.with_name(name)
                path.rename(new_path)
        else:
            dst_path, dst_root_id = self.path_from_id(parentId)
            if not dst_path:
                raise GirderException("Folder {} is not a mapping.".format(parentId))
            # Check wheter the user can write to the destination
            Folder().load(dst_root_id, user=user, level=AccessType.WRITE, exc=True)
            self.is_dir(dst_path, dst_root_id)
            new_path = dst_path / name
            shutil.move(path.as_posix(), new_path.as_posix())

        event.preventDefault().addResponse(
            Item().filter(self.vItem(new_path, root), user=user)
        )

    @access.user(scope=TokenScope.DATA_WRITE)
    @validate_event(level=AccessType.WRITE)
    def remove_item(self, event, path, root, user=None):
        self.is_file(path, root["_id"])
        path.unlink()
        event.preventDefault().addResponse({"message": "Deleted item %s." % path.name})

    @access.user(scope=TokenScope.DATA_WRITE)
    @validate_event(level=AccessType.WRITE)
    def copy_item(self, event, path, root, user=None):
        self.is_file(path, root["_id"])
        source = self.vItem(path, root)
        name = event.info["params"].get("name") or path.name

        folder_id = event.info["params"].get("folderId", source["folderId"])
        if str(folder_id).startswith("wtlocal:"):
            new_dirname, new_root_id = self.path_from_id(folder_id)
        else:
            new_root = Folder().load(folder_id, force=True, exc=True)
            try:
                new_dirname = pathlib.Path(new_root["fsPath"])
            except KeyError:
                raise GirderException("Folder {} is not a mapping.".format(folder_id))
            new_root_id = str(new_root["_id"])

        if new_root_id != str(root["_id"]):
            new_root = Folder().load(
                new_root["_id"], user=user, level=AccessType.WRITE, exc=True
            )  # Check ACLs
        else:
            new_root = root

        new_path = ensure_unique_path(new_dirname, name)
        shutil.copy(path.as_posix(), new_path.as_posix())
        event.preventDefault().addResponse(
            Item().filter(self.vItem(new_path, new_root), user=user)
        )

    @access.public(scope=TokenScope.DATA_READ)
    @validate_event(level=AccessType.READ)
    def get_child_files(self, event, path, root, user=None):
        event.preventDefault().addResponse(
            [File().filter(self.vFile(path, root), user=user)]
        )

    @access.public(scope=TokenScope.DATA_READ)
    @validate_event(level=AccessType.READ)
    def item_root_path(self, event, path, root, user=None):
        root_path = pathlib.Path(root["fsPath"])
        response = [
            dict(type="item", object=Item().filter(self.vItem(path, root), user=user))
        ]
        path = path.parent
        while path != root_path:
            response.append(
                dict(
                    type="folder",
                    object=Folder().filter(self.vFolder(path, root), user=user),
                )
            )
            path = path.parent

        response.append(dict(type="folder", object=Folder().filter(root, user=user)))
        girder_rootpath = Folder().parentsToRoot(root, user=user)
        response += girder_rootpath[::-1]
        response.pop(0)
        event.preventDefault().addResponse(response[::-1])
