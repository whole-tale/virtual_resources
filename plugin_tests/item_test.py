#!/usr/bin/env python
# -*- coding: utf-8 -*-

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


class ItemOperationsTestCase(base.TestCase):
    def setUp(self):
        super(ItemOperationsTestCase, self).setUp()
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
            {
                "email": "joel@dev.null",
                "login": "joel",
                "firstName": "Joel",
                "lastName": "CanTDoMuch",
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

    def test_basic_item_ops(self):
        from girder.plugins.virtual_resources.rest import VirtualObject

        root_path = pathlib.Path(self.public_folder["fsPath"])
        nested_dir = root_path / "level0"
        nested_dir.mkdir(parents=True)
        parentId = VirtualObject.generate_id(
            nested_dir.as_posix(), self.public_folder["_id"]
        )

        resp = self.request(
            path="/item",
            method="POST",
            user=self.users["admin"],
            params={"parentType": "folder", "parentId": parentId, "name": "test_item"},
        )
        self.assertStatusOk(resp)
        item = resp.json

        actual_item_path = nested_dir / item["name"]
        self.assertTrue(actual_item_path.is_file())

        decoded_path, decoded_root_id = VirtualObject.path_from_id(item["_id"])
        self.assertEqual(decoded_path, actual_item_path)
        self.assertEqual(decoded_root_id, str(self.public_folder["_id"]))

        resp = self.request(
            path="/item",
            method="GET",
            user=self.users["admin"],
            params={"parentType": "folder", "parentId": str(parentId)},
        )
        self.assertStatusOk(resp)
        get_items = resp.json
        self.assertEqual(len(get_items), 1)
        self.assertEqual(get_items[0], item)

        resp = self.request(
            path="/item/{_id}".format(**item), method="GET", user=self.users["admin"]
        )
        self.assertStatusOk(resp)
        self.assertEqual(resp.json, get_items[0])

        resp = self.request(
            path="/item/{_id}".format(**item),
            method="PUT",
            user=self.users["admin"],
            params={"name": "renamed"},
        )
        self.assertStatusOk(resp)
        item = resp.json
        self.assertFalse(actual_item_path.exists())
        actual_item_path = actual_item_path.with_name(item["name"])
        self.assertTrue(actual_item_path.is_file())

        resp = self.request(
            path="/item/{_id}/files".format(**item),
            method="GET",
            user=self.users["admin"],
        )
        self.assertStatusOk(resp)
        files = resp.json
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["_id"], item["_id"])

        resp = self.request(
            path="/item/{_id}".format(**item), method="DELETE", user=self.users["admin"]
        )
        self.assertStatusOk(resp)
        self.assertFalse(actual_item_path.exists())
        shutil.rmtree(nested_dir.as_posix())

    def test_item_rootpath(self):
        from girder.plugins.virtual_resources.rest import VirtualObject

        root_path = pathlib.Path(self.public_folder["fsPath"])
        nested_dir = root_path / "level0" / "level1"
        nested_dir.mkdir(parents=True)

        file1 = nested_dir / "some_file"
        file_contents = b"hello world\n"
        with file1.open(mode="wb") as fp:
            fp.write(file_contents)

        item_id = VirtualObject.generate_id(file1, self.public_folder["_id"])
        resp = self.request(
            path="/item/{}/rootpath".format(item_id),
            method="GET",
            user=self.users["admin"],
        )
        self.assertStatusOk(resp)
        self.assertEqual(len(resp.json), 4)
        rootpath = resp.json
        self.assertEqual(rootpath[0]["type"], "collection")
        self.assertEqual(rootpath[0]["object"]["_id"], str(self.base_collection["_id"]))
        self.assertEqual(rootpath[1]["type"], "folder")
        self.assertEqual(rootpath[1]["object"]["_id"], str(self.public_folder["_id"]))
        self.assertEqual(rootpath[2]["type"], "folder")
        self.assertEqual(rootpath[2]["object"]["name"], "level0")
        self.assertEqual(rootpath[3]["type"], "folder")
        self.assertEqual(rootpath[3]["object"]["name"], "level1")

        shutil.rmtree((root_path / "level0").as_posix())

    def test_copy_item(self):
        from girder.plugins.virtual_resources.rest import VirtualObject

        root_path = pathlib.Path(self.public_folder["fsPath"])
        file1 = root_path / "some_file"
        file_contents = b"hello world\n"
        with file1.open(mode="wb") as fp:
            fp.write(file_contents)
        item_id = VirtualObject.generate_id(file1, self.public_folder["_id"])

        # Copy in the same folder
        resp = self.request(
            path="/item/{}/copy".format(item_id),
            method="POST",
            user=self.users["admin"],
            params={"name": "copy"},
        )
        self.assertStatusOk(resp)

        self.assertTrue(file1.with_name("copy").is_file())
        with open(file1.with_name("copy").as_posix(), "rb") as fp:
            self.assertEqual(fp.read(), file_contents)

        # Copy in the same mapping but different folder
        new_dir = root_path / "some_dir"
        new_dir.mkdir()
        folder_id = VirtualObject.generate_id(new_dir, self.public_folder["_id"])
        resp = self.request(
            path="/item/{}/copy".format(item_id),
            method="POST",
            user=self.users["admin"],
            params={"folderId": str(folder_id)},
        )
        self.assertStatusOk(resp)
        new_file = new_dir / "some_file"
        self.assertTrue(new_file.is_file())
        with open(new_file.as_posix(), "rb") as fp:
            self.assertEqual(fp.read(), file_contents)

        # Copy between two mappings
        resp = self.request(
            path="/item/{}/copy".format(item_id),
            method="POST",
            user=self.users["admin"],
            params={"folderId": str(self.private_folder["_id"])},
        )
        self.assertStatusOk(resp)
        cors_file = pathlib.Path(self.private_folder["fsPath"]) / "some_file"
        self.assertTrue(cors_file.is_file())
        with open(cors_file.as_posix(), "rb") as fp:
            self.assertEqual(fp.read(), file_contents)

        # Try to copy to non mapping
        resp = self.request(
            path="/item/{}/copy".format(item_id),
            method="POST",
            user=self.users["admin"],
            params={"folderId": str(self.regular_folder["_id"])},
            exception=True,
        )
        self.assertStatus(resp, 500)
        self.assertEqual(
            resp.json["message"],
            "Folder {} is not a mapping.".format(self.regular_folder["_id"]),
        )

        shutil.rmtree(new_dir.as_posix())
        file1.unlink()
        file1.with_name("copy").unlink()
        cors_file.unlink()

    def tearDown(self):
        for folder in (self.public_folder, self.private_folder, self.regular_folder):
            Folder().remove(folder)
        Collection().remove(self.base_collection)
        for user in self.users.values():
            User().remove(user)
        for root in (self.public_root, self.private_root):
            shutil.rmtree(root)
        super(ItemOperationsTestCase, self).tearDown()