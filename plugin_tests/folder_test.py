#!/usr/bin/env python
# -*- coding: utf-8 -*-
import io
import pathlib
import shutil
import tempfile
import zipfile

from tests import base

from girder.models.collection import Collection
from girder.models.folder import Folder
from girder.models.user import User


def setUpModule():
    base.enabledPlugins.append("virtual_resources")
    base.startServer()


def tearDownModule():
    base.stopServer()


class FolderOperationsTestCase(base.TestCase):
    def setUp(self):
        super(FolderOperationsTestCase, self).setUp()
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
        self.shared_root = tempfile.mkdtemp()
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

        self.private_folder = Folder().createFolder(
            self.base_collection,
            "private",
            parentType="collection",
            public=True,
            reuseExisting=True,
        )
        self.private_folder.update(dict(fsPath=self.private_root, isMapping=True))
        self.private_folder = Folder().save(self.private_folder)

        self.regular_folder = Folder().createFolder(
            self.base_collection,
            "regular",
            creator=self.users["sally"],
            parentType="collection",
            public=True,
            reuseExisting=True,
        )

    def test_basic_folder_ops(self):
        from girder.plugins.virtual_resources.rest import VirtualObject

        resp = self.request(
            path="/folder",
            method="POST",
            user=self.users["admin"],
            params={
                "parentType": "folder",
                "parentId": self.public_folder["_id"],
                "name": "test_folder",
            },
        )
        self.assertStatusOk(resp)
        folder = resp.json

        actual_folder_path = pathlib.Path(self.public_root) / folder["name"]
        self.assertTrue(actual_folder_path.is_dir())

        decoded_path, decoded_root_id = VirtualObject.path_from_id(folder["_id"])
        self.assertEqual(decoded_path, actual_folder_path)
        self.assertEqual(decoded_root_id, str(self.public_folder["_id"]))

        resp = self.request(
            path="/folder",
            method="GET",
            user=self.users["admin"],
            params={"parentType": "folder", "parentId": str(self.public_folder["_id"])},
        )
        self.assertStatusOk(resp)
        get_folders = resp.json
        self.assertEqual(len(get_folders), 1)
        self.assertEqual(get_folders[0], folder)

        resp = self.request(
            path="/folder/{_id}".format(**folder),
            method="GET",
            user=self.users["admin"],
        )
        self.assertStatusOk(resp)
        self.assertEqual(resp.json, get_folders[0])

        resp = self.request(
            path="/folder/{_id}".format(**folder),
            method="PUT",
            user=self.users["admin"],
            params={"name": "renamed"},
        )
        self.assertStatusOk(resp)
        folder = resp.json
        self.assertFalse(actual_folder_path.exists())
        actual_folder_path = pathlib.Path(self.public_root) / folder["name"]
        self.assertTrue(actual_folder_path.is_dir())

        resp = self.request(
            path="/folder/{_id}".format(**folder),
            method="DELETE",
            user=self.users["admin"],
        )
        self.assertStatusOk(resp)
        self.assertFalse(actual_folder_path.exists())

    def test_folder_move(self):
        from girder.plugins.virtual_resources.rest import VirtualObject

        root_path = pathlib.Path(self.public_folder["fsPath"])
        dir1 = root_path / "some_dir"
        dir1.mkdir()
        file1 = dir1 / "some_file"
        with file1.open(mode="wb") as fp:
            fp.write(b"\n")

        folder_id = VirtualObject.generate_id(dir1, self.public_folder["_id"])

        resp = self.request(
            path="/folder/{}".format(folder_id),
            method="PUT",
            user=self.users["admin"],
            params={"name": dir1.name},
            exception=True,
        )
        self.assertStatus(resp, 500)
        self.assertEqual(
            resp.json["message"],
            "Folder '{}' already exists in {}".format(
                dir1.name, self.public_folder["_id"]
            ),
        )

        new_root_path = pathlib.Path(self.private_folder["fsPath"])
        dir2 = new_root_path / "level1"
        dir2.mkdir()
        new_folder_id = VirtualObject.generate_id(dir2, self.private_folder["_id"])

        resp = self.request(
            path="/folder/{}".format(folder_id),
            method="PUT",
            user=self.users["admin"],
            params={"parentId": new_folder_id, "parentType": "folder"},
        )
        self.assertStatusOk(resp)
        self.assertFalse(dir1.exists())
        self.assertTrue((dir2 / dir1.name).exists())
        new_file = dir2 / dir1.name / file1.name
        self.assertTrue(new_file.exists())
        new_file.unlink()
        (dir2 / dir1.name).rmdir()
        dir2.rmdir()

    def test_folder_details(self):
        root_path = pathlib.Path(self.public_folder["fsPath"])
        dir1 = root_path / "some_dir"
        dir1.mkdir()
        file1 = root_path / "some_file"
        with file1.open(mode="wb") as fp:
            fp.write(b"\n")

        resp = self.request(
            path="/folder/{_id}/details".format(**self.public_folder),
            method="GET",
            user=self.users["admin"],
        )
        self.assertStatusOk(resp)
        self.assertEqual(resp.json, {"nFolders": 1, "nItems": 1})

        file1.unlink()
        dir1.rmdir()

    def test_folder_rootpath(self):
        from girder.plugins.virtual_resources.rest import VirtualObject

        root_path = pathlib.Path(self.public_folder["fsPath"])
        nested_dir = root_path / "level0" / "level1" / "level2"
        nested_dir.mkdir(parents=True)

        folder_id = VirtualObject.generate_id(nested_dir, self.public_folder["_id"])
        resp = self.request(
            path="/folder/{}/rootpath".format(folder_id),
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

    def test_folder_delete_contents(self):
        from girder.plugins.virtual_resources.rest import VirtualObject

        root_path = pathlib.Path(self.public_folder["fsPath"])
        nested_dir = root_path / "lone_survivor"
        nested_dir.mkdir(parents=True)
        folder_id = VirtualObject.generate_id(nested_dir, self.public_folder["_id"])

        dir1 = nested_dir / "subfolder"
        dir1.mkdir()
        file1 = dir1 / "some_file.txt"
        with file1.open(mode="wb") as fp:
            fp.write(b"file1\n")
        file2 = nested_dir / "other_file.txt"
        with file2.open(mode="wb") as fp:
            fp.write(b"file2\n")
        self.assertEqual(len(list(nested_dir.iterdir())), 2)

        resp = self.request(
            path="/folder/{}/contents".format(folder_id),
            method="DELETE",
            user=self.users["admin"],
        )
        self.assertStatusOk(resp)
        self.assertEqual(len(list(nested_dir.iterdir())), 0)
        nested_dir.rmdir()

    def test_folder_download(self):
        from girder.plugins.virtual_resources.rest import VirtualObject

        root_path = pathlib.Path(self.public_folder["fsPath"])
        nested_dir = root_path / "lone_survivor"
        nested_dir.mkdir(parents=True)
        folder_id = VirtualObject.generate_id(nested_dir, self.public_folder["_id"])

        dir1 = nested_dir / "subfolder"
        dir1.mkdir()
        file1 = dir1 / "some_file.txt"
        with file1.open(mode="wb") as fp:
            fp.write(b"file1\n")
        file2 = nested_dir / "other_file.txt"
        with file2.open(mode="wb") as fp:
            fp.write(b"file2\n")
        self.assertEqual(len(list(nested_dir.iterdir())), 2)

        resp = self.request(
            path="/folder/{}/download".format(folder_id),
            method="GET",
            user=self.users["admin"],
            isJson=False,
        )
        self.assertStatusOk(resp)
        with zipfile.ZipFile(io.BytesIO(self.getBody(resp, text=False)), "r") as fp:
            self.assertEqual(
                sorted(fp.namelist()), ["other_file.txt", "subfolder/some_file.txt"]
            )
            # TODO should probably check the content too...

    def test_folder_copy(self):
        from girder.plugins.virtual_resources.rest import VirtualObject

        root_path = pathlib.Path(self.public_folder["fsPath"])
        dir1 = root_path / "source_folder"
        dir1.mkdir(parents=True)
        folder_id = VirtualObject.generate_id(dir1, self.public_folder["_id"])
        file1 = dir1 / "file.dat"
        with file1.open(mode="wb") as fp:
            fp.write(b"file1\n")

        resp = self.request(
            path="/folder/{}/copy".format(self.public_folder["_id"]),
            method="POST",
            user=self.users["sally"],
            params={"name": "new_copy"},
            exception=True,
        )
        self.assertStatus(resp, 500)
        self.assertEqual(resp.json["message"], "Copying mappings is not allowed.")

        resp = self.request(
            path="/folder/{}/copy".format(folder_id),
            method="POST",
            user=self.users["joel"],
            params={
                "name": "new_copy",
                "parentId": str(self.regular_folder["_id"]),
                "parentType": "folder",
            },
        )
        self.assertStatus(resp, 403)

        resp = self.request(
            path="/folder/{}/copy".format(folder_id),
            method="POST",
            user=self.users["sally"],
            params={
                "name": "new_copy",
                "parentId": str(self.regular_folder["_id"]),
                "parentType": "folder",
            },
            exception=True,
        )
        self.assertStatus(resp, 500)
        self.assertEqual(
            resp.json["message"],
            "Folder {} is not a mapping.".format(self.regular_folder["_id"]),
        )

        resp = self.request(
            path="/folder/{}/copy".format(folder_id),
            method="POST",
            user=self.users["sally"],
            params={},
            exception=True,
        )
        self.assertStatus(resp, 500)
        self.assertEqual(
            resp.json["message"],
            "Folder '{}' already exists at {}".format(dir1.name, dir1),
        )

        resp = self.request(
            path="/folder/{}/copy".format(folder_id),
            method="POST",
            user=self.users["sally"],
            params={"name": "new_copy"},
        )
        self.assertStatus(resp, 403)
        self.assertFalse((dir1.with_name("new_copy") / file1.name).is_file())

        resp = self.request(
            path="/folder/{}/copy".format(folder_id),
            method="POST",
            user=self.users["admin"],
            params={"name": "new_copy"},
        )
        self.assertStatusOk(resp)
        new_folder = resp.json
        self.assertEqual(new_folder["name"], "new_copy")
        self.assertTrue((dir1.with_name("new_copy") / file1.name).is_file())

        resp = self.request(
            path="/folder/{}/copy".format(folder_id),
            method="POST",
            user=self.users["admin"],
            params={
                "name": "copy_within_copy",
                "parentId": new_folder["_id"],
                "parentType": "folder",
            },
        )
        self.assertStatusOk(resp)
        self.assertTrue(
            (dir1.with_name("new_copy") / "copy_within_copy" / file1.name).is_file()
        )

    def tearDown(self):
        Folder().remove(self.public_folder)
        Folder().remove(self.private_folder)
        Folder().remove(self.regular_folder)
        Collection().remove(self.base_collection)
        for user in self.users.values():
            User().remove(user)
        for root in (self.public_root, self.shared_root, self.private_root):
            shutil.rmtree(root)
        super(FolderOperationsTestCase, self).tearDown()
