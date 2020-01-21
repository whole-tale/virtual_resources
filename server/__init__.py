#!/usr/bin/env python
# -*- coding: utf-8 -*-
from girder import events
from girder.api.v1.folder import Folder as FolderResource
from girder.api.rest import boundHandler
from girder.constants import AccessType
from girder.models.folder import Folder

from .rest.virtual_item import VirtualItem
from .rest.virtual_file import VirtualFile
from .rest.virtual_folder import VirtualFolder
from .rest.virtual_resource import VirtualResource


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

    info["apiRoot"].virtual_item = VirtualItem()
    virtual_file = VirtualFile()
    info["apiRoot"].virtual_file = virtual_file
    info["apiRoot"].virtual_folder = VirtualFolder()
    info["apiRoot"].virtual_resource = VirtualResource()
    events.bind(
        "rest.get.item/:id/download.before", info["name"], virtual_file.file_download
    )
