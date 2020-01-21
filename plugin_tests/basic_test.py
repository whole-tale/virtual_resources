#!/usr/bin/env python
# -*- coding: utf-8 -*-

import pathlib
import pytest
import random
import shutil
import string
import tempfile

from tests import base

from girder.exceptions import ValidationException
from girder.models.collection import Collection
from girder.models.folder import Folder
from girder.models.user import User

chunk1, chunk2 = ("hello ", "world")
chunkData = chunk1.encode("utf8") + chunk2.encode("utf8")


def random_string(length=10):
    """Generate a random string of fixed length."""
    letters = string.ascii_lowercase
    return "".join(random.choice(letters) for i in range(length))


def setUpModule():
    base.enabledPlugins.append("virtual_resources")
    base.startServer()


def tearDownModule():
    base.stopServer()


class BasicOperationsTestCase(base.TestCase):
    def setUp(self):
        super(BasicOperationsTestCase, self).setUp()
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

        self.base_collection = Collection().createCollection(
            random_string(),
            creator=self.users["admin"],
            public=True,
            reuseExisting=True,
        )

        self.public_folder = Folder().createFolder(
            self.base_collection,
            "public",
            creator=self.users["sally"],
            parentType="collection",
            public=True,
            reuseExisting=True,
        )
        self.public_folder.update(dict(fsPath=self.public_root, isMapping=True))
        self.public_folder = Folder().save(self.public_folder)

    def test_vo_methods(self):
        from girder.plugins.virtual_resources.rest import VirtualObject as vo

        root_path = pathlib.Path(self.public_folder["fsPath"])
        non_existing = root_path / "something"

        with pytest.raises(ValidationException):
            vo().is_file(non_existing, self.public_folder["_id"])

        with pytest.raises(ValidationException):
            vo().is_dir(non_existing, self.public_folder["_id"])

    def test_mapping_creation(self):
        new_folder = Folder().createFolder(
            self.base_collection,
            random_string(),
            creator=self.users["sally"],
            parentType="collection",
            public=True,
            reuseExisting=True,
        )

        resp = self.request(
            path="/folder/{_id}".format(**new_folder),
            method="PUT",
            user=self.users["sally"],
            params={"fsPath": "/etc", "isMapping": True},  # H4ck3r detected!
        )
        self.assertStatus(resp, 403)
        self.assertEqual(
            resp.json,
            {"message": "Must be admin to setup virtual folders.", "type": "access"},
        )

        resp = self.request(
            path="/folder/{_id}".format(**new_folder),
            method="PUT",
            user=self.users["admin"],
            params={"fsPath": "/etc", "isMapping": True},  # G0d
        )
        self.assertStatusOk(resp)
        self.assertHasKeys(resp.json, ["fsPath", "isMapping"])
        self.assertEqual(resp.json["fsPath"], "/etc")
        self.assertEqual(resp.json["isMapping"], True)

        Folder().remove(new_folder)

    def tearDown(self):
        Folder().remove(self.public_folder)
        Collection().remove(self.base_collection)
        for user in self.users.values():
            User().remove(user)
        for root in (self.public_root,):
            shutil.rmtree(root)
        super(BasicOperationsTestCase, self).tearDown()
