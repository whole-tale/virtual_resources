#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pathlib
from operator import itemgetter

from girder import events
from girder.exceptions import ValidationException
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
        # DELETE /folder/:id
        # GET /folder/:id/access
        # PUT /folder/:id/access
        # PUT /folder/:id/check
        # DELETE /folder/:id/contents
        # POST /folder/:id/copy
        events.bind("rest.get.folder/:id/details.before", name, self.get_folder_details)
        # GET /folder/:id/download
        # PUT/DELETE /folder/:id/metadata
        events.bind("rest.get.folder/:id/rootpath.before", name, self.folder_root_path)

    @validate_event
    def get_child_folders(self, event, path, root_id):
        response = [
            self.vFolder(obj, root_id) for obj in path.iterdir() if obj.is_dir()
        ]
        event.preventDefault().addResponse(sorted(response, key=itemgetter("name")))

    @validate_event
    def create_folder(self, event, path, root_id):
        params = event.info["params"]
        new_path = path / params["name"]
        new_path.mkdir()
        event.preventDefault().addResponse(self.vFolder(new_path, root_id))

    @validate_event
    def get_folder_info(self, event, path, root_id):
        event.preventDefault().addResponse(self.vFolder(path, root_id))

    @validate_event
    def rename_folder(self, event, path, root_id):
        if not (path.exists() and path.is_dir()):
            raise ValidationException(
                "Invalid ObjectId: %s" % self.generate_id(path, root_id), field="id"
            )

        new_path = path.with_name(event.info["params"]["name"])
        path.rename(new_path)
        event.preventDefault().addResponse(self.vFolder(new_path, root_id))

    @validate_event
    def get_folder_details(self, event, path, root_id):
        if not (path.exists() and path.is_dir()):
            raise ValidationException(
                "Invalid ObjectId: %s" % self.generate_id(path, root_id), field="id"
            )

        response = dict(nFolders=0, nItems=0)
        for obj in path.iterdir():
            if obj.is_dir():
                response["nFolders"] += 1
            elif obj.is_file():
                response["nItems"] += 1
        event.preventDefault().addResponse(response)

    @validate_event
    def folder_root_path(self, event, path, root_id):
        user = self.getCurrentUser()
        root_folder = Folder().load(root_id, force=True)
        root_path = pathlib.Path(root_folder["fsPath"])

        response = [dict(type="folder", object=self.vFolder(path, root_id))]
        path = path.parent
        while path != root_path:
            response.append(dict(type="folder", object=self.vFolder(path, root_id)))
            path = path.parent

        response.append(dict(type="folder", object=Folder().filter(root_folder, user)))
        girder_rootpath = Folder().parentsToRoot(
            root_folder, user=self.getCurrentUser()
        )
        response += girder_rootpath[::-1]
        response.pop(0)
        event.preventDefault().addResponse(response[::-1])
