#!/usr/bin/env python
# -*- coding: utf-8 -*-
from girder import events
from girder.api.v1.folder import Folder as FolderResource
from girder.constants import AccessType
from girder.models.folder import Folder
from girder.plugin import GirderPlugin

from .rest.virtual_item import VirtualItem
from .rest.virtual_file import VirtualFile
from .rest.virtual_folder import VirtualFolder
from .rest.virtual_resource import VirtualResource


class VirtualResourcesPlugin(GirderPlugin):
    DISPLAY_NAME = "Virtual Resources"
    CLIENT_SOURCE_PATH = "web_client"

    def load(self, info):
        plugin_name = self.DISPLAY_NAME.lower().replace(" ", "_")
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
            "rest.get.item/:id/download.before", plugin_name, virtual_file.file_download
        )
