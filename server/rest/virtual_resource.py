#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
from operator import itemgetter
import os
import pathlib
import shutil

from girder import events

from girder.api import access
from girder.constants import AccessType, TokenScope
from girder.exceptions import (
    AccessException,
    ValidationException,
    ResourcePathNotFound,
    RestException,
)
from girder.models.collection import Collection
from girder.models.folder import Folder
from girder.models.user import User
from girder.utility.path import lookUpToken, split, getResourcePath
from girder.utility.model_importer import ModelImporter
from girder.utility.progress import ProgressContext

from . import VirtualObject, validate_event, ensure_unique_path


class EmptyDocument(Exception):
    pass


class VirtualResource(VirtualObject):
    def __init__(self):
        super(VirtualResource, self).__init__()
        self.resourceName = "virtual_resource"
        name = "virtual_resources"

        events.bind("rest.delete.resource.before", name, self.delete_resources)
        # GET /resource/:id
        events.bind("rest.get.resource/:id/path.before", name, self.path)
        # PUT /resource/:id/timestamp
        events.bind("rest.post.resource/copy.before", name, self.copy_resources)
        # GET /resource/:id/download
        events.bind("rest.get.resource/lookup.before", name, self.lookup)
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
                    name = source_path.name
                    new_path = ensure_unique_path(path, name)
                    shutil.copy(source_path.as_posix(), new_path.as_posix())
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

    @access.public(scope=TokenScope.DATA_READ)
    @validate_event(level=AccessType.READ)
    def path(self, event, path, root, user=None):
        res_type = event.info["params"]["type"]
        try:
            if res_type == "folder":
                self.is_dir(path, root)
            elif res_type in ("item", "file"):
                self.is_file(path, root)
            else:
                raise ValidationException("Not a file, nor a folder")
        except ValidationException:
            raise RestException("Invalid resource id.")

        root_girder_path = pathlib.Path(getResourcePath("folder", root, user=user))
        remainder_path = path.relative_to(pathlib.PosixPath(root["fsPath"]))
        response = (root_girder_path / remainder_path).as_posix()
        event.preventDefault().addResponse(response)

    @access.public(scope=TokenScope.DATA_READ)
    def lookup(self, event):
        test = event.info["params"].get("test", False)
        path = event.info["params"].get("path")
        response = self._lookUpPath(path, self.getCurrentUser(), test)["document"]
        event.preventDefault().addResponse(response)

    @staticmethod
    def _lookup_err(msg, test=False):
        if test:
            raise EmptyDocument
        else:
            raise ResourcePathNotFound(msg)

    def _get_base(self, pathArray, test=False):
        model = pathArray[0]
        if model == "user":
            username = pathArray[1]
            parent = User().findOne({"login": username})
            if parent is None:
                self._lookup_err("User not found: %s" % username, test=test)
        elif model == "collection":
            collectionName = pathArray[1]
            parent = Collection().findOne({"name": collectionName})
            if parent is None:
                self._lookup_err("Collection not found: %s" % collectionName, test=test)
        else:
            raise ValidationException("Invalid path format")
        return parent, model

    def _get_vobject(self, document, path, i):
        pathArray = split(path)
        root = document
        n = 3 + i
        fspath = os.path.join(document["fsPath"], "/".join(pathArray[n:]))
        fspath = pathlib.Path(fspath)
        if not fspath.exists():
            raise ValidationException("Path not found: %s" % path)
        if fspath.is_dir():
            document = self.vFolder(fspath, root)
            model = "folder"
        elif fspath.is_file():
            document = self.vItem(fspath, root)
            model = "item"
        # TODO: add vLink here...
        return document, model

    def _lookUpPath(self, path, user=None, test=False, filter=True, force=False):
        """
        Look up a resource in the data hierarchy by path.

        :param path: path of the resource
        :param user: user with correct privileges to access path
        :param test: defaults to false, when set to true
            will return None instead of throwing exception when
            path doesn't exist
        :type test: bool
        :param filter: Whether the returned model should be filtered.
        :type filter: bool
        :param force: if True, don't validate the access.
        :type force: bool
        """
        path = path.lstrip("/")
        pathArray = split(path)

        try:
            document, model = self._get_base(pathArray, test=test)
        except EmptyDocument:
            return {"model": None, "document": None}

        try:
            if not force:
                ModelImporter.model(model).requireAccess(document, user)
            token = None
            for i, token in enumerate(pathArray[2:]):  # noqa
                document, model = lookUpToken(token, model, document)
                if not force:
                    ModelImporter.model(model).requireAccess(document, user)
                if "fsPath" in document:
                    break
            if token != pathArray[-1]:
                document, model = self._get_vobject(document, path, i)
        except (ValidationException, AccessException):
            if test:
                return {"model": None, "document": None}
            raise ResourcePathNotFound("Path not found: %s" % path)

        if filter:
            document = ModelImporter.model(model).filter(document, user)

        return {"model": model, "document": document}
