#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
from operator import itemgetter
import shutil

from girder import events

from girder.api import access
from girder.constants import AccessType, TokenScope
from girder.exceptions import AccessException
from girder.models.folder import Folder
from girder.utility.progress import ProgressContext

from . import VirtualObject, validate_event


class VirtualResource(VirtualObject):
    def __init__(self):
        super(VirtualResource, self).__init__()
        self.resourceName = "virtual_resource"
        name = "virtual_resources"

        events.bind("rest.delete.resource.before", name, self.delete_resources)
        # GET /resource/:id
        # GET /resource/:id/path
        # PUT /resource/:id/timestamp
        events.bind("rest.post.resource/copy.before", name, self.copy_resources)
        # GET /resource/:id/download
        # GET /resource/lookup
        events.bind("rest.put.resource/move.before", name, self.move_resources)
        # GET /resource/search

    def _filter_resources(self, event, level=AccessType.WRITE, user=None):
        resources = json.loads(event.info["params"]["resources"])
        remaining_resources = dict(folder=[], item=[])
        wt_resources = []
        for kind in resources:
            for obj_id in resources[kind]:
                if obj_id.startswith("wtlocal:"):
                    source_path, root_id = self.path_from_id(obj_id)
                    try:
                        root = Folder().load(root_id, user=user, level=level, exc=False)
                    except AccessException:
                        root = None
                    if root:
                        wt_resources.append({"src_path": source_path, "kind": kind})
                else:
                    remaining_resources[kind].append(obj_id)
        total = sum([len(remaining_resources[key]) for key in remaining_resources])
        event.info["params"]["resources"] = json.dumps(remaining_resources)
        if total == 0:
            event.preventDefault().addResponse(None)
        return sorted(
            wt_resources, key=itemgetter("kind"), reverse=True
        )  # We want to have items first, which is relevant for MOVE op

    @access.user(scope=TokenScope.DATA_OWN)
    def delete_resources(self, event):
        user = self.getCurrentUser()
        wt_resources = self._filter_resources(event, level=AccessType.WRITE, user=user)
        progress = event.info["params"].get("progress", False)
        with ProgressContext(
            progress,
            user=user,
            title="Deleting resources",
            message="Calculating requirements...",
            total=len(wt_resources),
        ) as ctx:
            for obj in wt_resources:
                source_path = obj["src_path"]
                ctx.update(message="Deleting %s %s" % (obj["kind"], source_path.name))
                if obj["kind"] == "folder":
                    shutil.rmtree(source_path.as_posix())
                else:
                    source_path.unlink()
                ctx.update(increment=1)

    @access.user(scope=TokenScope.DATA_WRITE)
    @validate_event(level=AccessType.WRITE)
    def copy_resources(self, event, path, root, user=None):
        wt_resources = self._filter_resources(event, level=AccessType.READ, user=user)
        progress = event.info["params"].get("progress", False)
        with ProgressContext(
            progress,
            user=user,
            title="Copying resources",
            message="Calculating requirements...",
            total=len(wt_resources),
        ) as ctx:
            for obj in wt_resources:
                source_path = obj["src_path"]
                ctx.update(message="Copying %s %s" % (obj["kind"], source_path.name))
                if obj["kind"] == "folder":
                    shutil.copytree(
                        source_path.as_posix(), (path / source_path.name).as_posix()
                    )
                else:
                    shutil.copy(
                        source_path.as_posix(), (path / source_path.name).as_posix()
                    )
                ctx.update(increment=1)

    @access.user(scope=TokenScope.DATA_WRITE)
    @validate_event(level=AccessType.WRITE)
    def move_resources(self, event, path, root, user=None):
        wt_resources = self._filter_resources(event, level=AccessType.WRITE, user=user)
        progress = event.info["params"].get("progress", False)
        with ProgressContext(
            progress,
            user=user,
            title="Moving resources",
            message="Calculating requirements...",
            total=len(wt_resources),
        ) as ctx:
            for obj in wt_resources:
                source_path = obj["src_path"]
                ctx.update(message="Moving %s %s" % (obj["kind"], source_path.name))
                shutil.move(
                    source_path.as_posix(), (path / source_path.name).as_posix()
                )
                ctx.update(increment=1)
