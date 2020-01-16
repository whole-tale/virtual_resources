#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
import shutil

from girder import events
from girder.api import access
from girder.constants import TokenScope
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

    @access.user(scope=TokenScope.DATA_OWN)
    def delete_resources(self, event, root_id):
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
                    source_path, root_id = self.path_from_id(obj_id)
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

    @access.user(scope=TokenScope.DATA_WRITE)
    @validate_event
    def copy_resources(self, event, path, root_id):
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
                    source_path, root_id = self.path_from_id(obj_id)
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

    @access.user(scope=TokenScope.DATA_WRITE)
    @validate_event
    def move_resources(self, event, path, root_id):
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
                    source_path, root_id = self.path_from_id(obj_id)
                    ctx.update(message="Moving %s %s" % (kind, source_path.name))
                    shutil.move(
                        source_path.as_posix(), (path / source_path.name).as_posix()
                    )
                    ctx.update(increment=1)
        event.preventDefault().addResponse(None)
