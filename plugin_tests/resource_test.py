#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import pathlib
import shutil
import tempfile

from tests import base

from girder.models.collection import Collection
from girder.models.folder import Folder
from girder.models.user import User


def setUpModule():
    base.enabledPlugins.append("virtual_resources")
    base.startServer()


def tearDownModule():
    base.stopServer()


class ResourceOperationsTestCase(base.TestCase):
    def setUp(self):
        super(ResourceOperationsTestCase, self).setUp()
        users = (
            {
                "email": "root@dev.null",
                "login": "admin",
                "firstName": "Jane",
                "lastName": "Austin",
                "password": "secret",
            },
            {
                "email": "sally@dev.null",
                "login": "sally",
                "firstName": "Sally",
                "lastName": "User",
                "password": "secret",
            },
        )

        self.users = {user["login"]: User().createUser(**user) for user in users}

        self.public_root = tempfile.mkdtemp()
        self.private_root = tempfile.mkdtemp()

        self.base_collection = Collection().createCollection(
            "Virtual Resources",
            creator=self.users["admin"],
            public=True,
            reuseExisting=True,
        )

        self.public_folder = Folder().createFolder(
            self.base_collection,
            "public",
            parentType="collection",
            public=True,
            reuseExisting=True,
        )
        self.public_folder.update(dict(fsPath=self.public_root, isMapping=True))
        self.public_folder = Folder().save(self.public_folder)

        self.regular_folder = Folder().createFolder(
            self.base_collection,
            "public_no_map",
            parentType="collection",
            public=True,
            reuseExisting=True,
        )

        self.private_folder = Folder().createFolder(
            self.base_collection,
            "private",
            creator=self.users["sally"],
            parentType="collection",
            public=False,
            reuseExisting=True,
        )
        self.private_folder.update(dict(fsPath=self.private_root, isMapping=True))
        self.private_folder = Folder().save(self.private_folder)

    def test_basic_ops(self):
        from girder.plugins.virtual_resources.rest import VirtualObject

        root_path = pathlib.Path(self.public_folder["fsPath"])
        nested_dir = root_path / "level0"
        nested_dir.mkdir(parents=True)

        file1 = root_path / "some_file.txt"
        file1_contents = b"Blah Blah Blah"
        with file1.open(mode="wb") as fp:
            fp.write(file1_contents)

        file2 = nested_dir / "some_other_file.txt"
        file2_contents = b"Lorem ipsum..."
        with file2.open(mode="wb") as fp:
            fp.write(file2_contents)

        # COPY
        copy_target_dir = root_path / "copy_dest"
        copy_target_dir.mkdir()

        resources = {
            "item": [
                VirtualObject.generate_id(file1.as_posix(), self.public_folder["_id"])
            ],
            "folder": [
                VirtualObject.generate_id(
                    nested_dir.as_posix(), self.public_folder["_id"]
                )
            ],
        }

        parentId = VirtualObject.generate_id(
            copy_target_dir.as_posix(), self.public_folder["_id"]
        )

        resp = self.request(
            path="/resource/copy",
            method="POST",
            user=self.users["admin"],
            params={
                "parentType": "folder",
                "parentId": parentId,
                "resources": json.dumps(resources),
            },
        )
        self.assertStatusOk(resp)

        copied_dir = copy_target_dir / nested_dir.name
        self.assertTrue(copied_dir.is_dir())
        copied_file1 = copy_target_dir / file1.name
        self.assertTrue(copied_file1.is_file())
        with copied_file1.open(mode="rb") as fp:
            self.assertEqual(fp.read(), file1_contents)
        copied_file2 = copied_dir / file2.name
        self.assertTrue(copied_file2.is_file())
        with copied_file2.open(mode="rb") as fp:
            self.assertEqual(fp.read(), file2_contents)

        # MOVE
        move_target_dir = root_path / "moved"
        move_target_dir.mkdir()
        resources = {
            "item": [
                VirtualObject.generate_id(file1.as_posix(), self.public_folder["_id"]),
                VirtualObject.generate_id(
                    copied_file2.as_posix(), self.public_folder["_id"]
                ),
            ],
            "folder": [
                VirtualObject.generate_id(
                    copy_target_dir.as_posix(), self.public_folder["_id"]
                )
            ],
        }
        parentId = VirtualObject.generate_id(
            move_target_dir.as_posix(), self.public_folder["_id"]
        )
        resp = self.request(
            path="/resource/move",
            method="PUT",
            user=self.users["admin"],
            params={
                "parentType": "folder",
                "parentId": parentId,
                "resources": json.dumps(resources),
            },
        )
        self.assertStatusOk(resp)
        for resource in (copy_target_dir, file1, copied_file2):
            self.assertFalse(resource.exists())

        moved_dir = move_target_dir / copy_target_dir.name
        moved_file1 = move_target_dir / file1.name
        moved_file2 = move_target_dir / file2.name
        self.assertTrue(moved_dir.is_dir())
        self.assertTrue(moved_file1.is_file())
        self.assertTrue(moved_file2.is_file())

        # DELETE
        with file1.open(mode="wb") as fp:  # Recreate
            fp.write(file1_contents)

        resources = {"item": [], "folder": []}
        for path in root_path.iterdir():
            if path.is_dir():
                key = "folder"
            else:
                key = "item"
            resources[key].append(
                VirtualObject.generate_id(path.as_posix(), self.public_folder["_id"])
            )

        resp = self.request(
            path="/resource",
            method="DELETE",
            user=self.users["admin"],
            params={"resources": json.dumps(resources)},
        )
        self.assertStatusOk(resp)
        self.assertEqual(len(list(root_path.iterdir())), 0)

    def test_resource_op_acls(self):
        from girder.plugins.virtual_resources.rest import VirtualObject

        root_path = pathlib.Path(self.public_folder["fsPath"])
        nested_dir = root_path / "level0"
        nested_dir.mkdir(parents=True)

        file1 = root_path / "some_file.txt"
        file1_contents = b"Blah Blah Blah"
        with file1.open(mode="wb") as fp:
            fp.write(file1_contents)
        
        sallys_root_path = pathlib.Path(self.private_folder["fsPath"])
        # MOVE
        move_target_dir = sallys_root_path / "moved"
        move_target_dir.mkdir()
        resources = {
            "item": [
                VirtualObject.generate_id(file1.as_posix(), self.public_folder["_id"]),
            ],
            "folder": [
                VirtualObject.generate_id(
                    nested_dir.as_posix(), self.public_folder["_id"]
                )
            ],
        }
        parentId = VirtualObject.generate_id(
            move_target_dir.as_posix(), self.private_folder["_id"]
        )
        resp = self.request(
            path="/resource/move",
            method="PUT",
            user=self.users["sally"],
            params={
                "parentType": "folder",
                "parentId": parentId,
                "resources": json.dumps(resources),
            },
        )
        self.assertStatusOk(resp)
        self.assertTrue(nested_dir.exists())
        self.assertTrue(file1.exists())
        self.assertEqual(len(list(move_target_dir.iterdir())), 0)
       
        # COPY cors
        resp = self.request(
            path="/resource/copy",
            method="POST",
            user=self.users["sally"],
            params={
                "parentType": "folder",
                "parentId": parentId,
                "resources": json.dumps(resources),
            },
        )
        self.assertStatusOk(resp)
        self.assertTrue((move_target_dir / nested_dir.name).is_dir())
        self.assertTrue((move_target_dir / file1.name).is_file())

    def tearDown(self):
        for folder in (self.public_folder, self.private_folder, self.regular_folder):
            Folder().remove(folder)
        Collection().remove(self.base_collection)
        for user in self.users.values():
            User().remove(user)
        for root in (self.public_root, self.private_root):
            shutil.rmtree(root)
        super(ResourceOperationsTestCase, self).tearDown()
