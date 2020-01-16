#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import pathlib
from operator import itemgetter
import shutil

from girder import events
from girder.exceptions import ValidationException
from girder.models.folder import Folder
from girder.models.item import Item

from . import VirtualObject, validate_event


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

    @validate_event
    def get_child_items(self, event, path, root_id):
        response = [self.vItem(obj, root_id) for obj in path.iterdir() if obj.is_file()]
        event.preventDefault().addResponse(sorted(response, key=itemgetter("name")))

    @validate_event
    def create_item(self, event, path, root_id):
        params = event.info["params"]
        new_path = path / params["name"]
        with open(new_path, "a"):
            os.utime(new_path.as_posix())
        event.preventDefault().addResponse(self.vItem(new_path, root_id))

    @validate_event
    def get_item_info(self, event, path, root_id):
        event.preventDefault().addResponse(self.vItem(path, root_id))

    @validate_event
    def rename_item(self, event, path, root_id):
        if not (path.exists() and path.is_file()):
            raise ValidationException(
                "Invalid ObjectId: %s" % self.generate_id(path, root_id), field="id"
            )

        new_path = path.with_name(event.info["params"]["name"])
        path.rename(new_path)
        event.preventDefault().addResponse(self.vItem(new_path, root_id))

    @validate_event
    def remove_item(self, event, path, root_id):
        if not (path.exists() and path.is_file()):
            raise ValidationException(
                "Invalid ObjectId: %s" % self.generate_id(path, root_id), field="id"
            )

        path.unlink()
        event.preventDefault().addResponse({"message": "Deleted item %s." % path.name})

    @validate_event
    def copy_item(self, event, path, root_id):
        # TODO: folderId is not passed properly, but that's vanilla girder's fault...
        if not (path.exists() and path.is_file()):
            raise ValidationException(
                "Invalid ObjectId: %s" % self.generate_id(path, root_id), field="id"
            )

        name = event.info["params"].get("name") or path.name
        path, root_id = self.path_from_id(event.info["params"]["folderId"])
        new_path = path / name
        shutil.copy(path.as_posix(), new_path.as_posix())
        event.preventDefault().addResponse(self.vItem(new_path, root_id))

    @validate_event
    def get_child_files(self, event, path, root_id):
        event.preventDefault().addResponse([self.vFile(path, root_id)])

    @validate_event
    def item_root_path(self, event, path, root_id):
        user = self.getCurrentUser()
        root_folder = Folder().load(root_id, force=True)
        root_path = pathlib.Path(root_folder["fsPath"])

        response = [
            dict(type="item", object=Item().filter(self.vItem(path, root_id), user))
        ]
        path = path.parent
        while path != root_path:
            response.append(
                dict(
                    type="folder",
                    object=Folder().filter(self.vFolder(path, root_id), user),
                )
            )
            path = path.parent

        response.append(dict(type="folder", object=Folder().filter(root_folder, user)))
        girder_rootpath = Folder().parentsToRoot(
            root_folder, user=self.getCurrentUser()
        )
        response += girder_rootpath[::-1]
        response.pop(0)
        event.preventDefault().addResponse(response[::-1])
