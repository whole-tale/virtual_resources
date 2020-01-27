#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pathlib
from operator import itemgetter
import shutil

from girder import events
from girder.api import access
from girder.constants import TokenScope, AccessType
from girder.models.folder import Folder

from . import VirtualObject, validate_event


class VirtualFolder(VirtualObject):
    def __init__(self):
        super(VirtualFolder, self).__init__()
        self.resourceName = "virtual_folder"
        name = "virtual_resources"
        events.bind("rest.get.folder.before", name, self.get_child_folders)
        events.bind("rest.post.folder.before", name, self.create_folder)
        events.bind("rest.get.folder/:id.before", name, self.get_folder_info)
        events.bind("rest.put.folder/:id.before", name, self.rename_folder)
        events.bind("rest.delete.folder/:id.before", name, self.remove_folder)
        # GET /folder/:id/access -- not needed
        # PUT /folder/:id/access -- not needed
        # PUT /folder/:id/check -- not needed
        events.bind(
            "rest.delete.folder/:id/contents.before", name, self.remove_folder_contents
        )
        # POST /folder/:id/copy
        events.bind("rest.get.folder/:id/details.before", name, self.get_folder_details)
        # GET /folder/:id/download
        # PUT/DELETE /folder/:id/metadata -- not needed
        events.bind("rest.get.folder/:id/rootpath.before", name, self.folder_root_path)

    @access.public(scope=TokenScope.DATA_READ)
    @validate_event(level=AccessType.READ)
    def get_child_folders(self, event, path, root, user=None):
        response = [
            Folder().filter(self.vFolder(obj, root), user=user)
            for obj in path.iterdir()
            if obj.is_dir()
        ]
        event.preventDefault().addResponse(sorted(response, key=itemgetter("name")))

    @access.user(scope=TokenScope.DATA_WRITE)
    @validate_event(level=AccessType.WRITE)
    def create_folder(self, event, path, root, user=None):
        params = event.info["params"]
        new_path = path / params["name"]
        new_path.mkdir()
        event.preventDefault().addResponse(
            Folder().filter(self.vFolder(new_path, root), user=user)
        )

    @access.public(scope=TokenScope.DATA_READ)
    @validate_event(level=AccessType.READ)
    def get_folder_info(self, event, path, root, user=None):
        event.preventDefault().addResponse(
            Folder().filter(self.vFolder(path, root), user)
        )

    @access.user(scope=TokenScope.DATA_WRITE)
    @validate_event(level=AccessType.WRITE)
    def rename_folder(self, event, path, root, user=None):
        self.is_dir(path, root["_id"])
        new_path = path.with_name(event.info["params"]["name"])
        path.rename(new_path)
        event.preventDefault().addResponse(
            Folder().filter(self.vFolder(new_path, root), user=user)
        )

    @access.user(scope=TokenScope.DATA_WRITE)
    @validate_event(level=AccessType.WRITE)
    def remove_folder(self, event, path, root, user=None):
        self.is_dir(path, root["_id"])
        shutil.rmtree(path.as_posix())
        event.preventDefault().addResponse(
            {"message": "Deleted folder %s." % path.name}
        )

    @access.user(scope=TokenScope.DATA_WRITE)
    @validate_event(level=AccessType.WRITE)
    def remove_folder_contents(self, event, path, root, user=None):
        self.is_dir(path, root["_id"])
        for sub_path in path.iterdir():
            if sub_path.is_file():
                sub_path.unlink()
            elif sub_path.is_dir():
                shutil.rmtree(sub_path.as_posix())
        event.preventDefault().addResponse(
            {"message": "Deleted contents of folder %s." % path.name}
        )

    @access.public(scope=TokenScope.DATA_READ)
    @validate_event(level=AccessType.READ)
    def get_folder_details(self, event, path, root, user=None):
        self.is_dir(path, root["_id"])
        response = dict(nFolders=0, nItems=0)
        for obj in path.iterdir():
            if obj.is_dir():
                response["nFolders"] += 1
            elif obj.is_file():
                response["nItems"] += 1
        event.preventDefault().addResponse(response)

    @access.public(scope=TokenScope.DATA_READ)
    @validate_event(level=AccessType.READ)
    def folder_root_path(self, event, path, root, user=None):
        root_path = pathlib.Path(root["fsPath"])
        response = []
        if root_path != path:
            response.append(
                dict(
                    type="folder",
                    object=Folder().filter(self.vFolder(path, root), user=user),
                )
            )
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
        girder_rootpath = Folder().parentsToRoot(root, user=self.getCurrentUser())
        response += girder_rootpath[::-1]
        response.pop(0)
        event.preventDefault().addResponse(response[::-1])
