#!/usr/bin/env python
# -*- coding: utf-8 -*-

import pathlib
import pytest
import random
import shutil
import string
import tempfile

from girder.exceptions import ValidationException
from girder.models.collection import Collection
from girder.models.folder import Folder
from pytest_girder.assertions import assertStatusOk, assertStatus

chunk1, chunk2 = ("hello ", "world")
chunkData = chunk1.encode("utf8") + chunk2.encode("utf8")


def random_string(length=10):
    """Generate a random string of fixed length."""
    letters = string.ascii_lowercase
    return "".join(random.choice(letters) for i in range(length))


@pytest.fixture
def public_collection(db, user):
    public_collection = Collection().createCollection(
        "test_collection",
        creator=user,
        public=True,
        reuseExisting=True,
    )
    yield public_collection
    Collection().remove(public_collection)


@pytest.fixture
def public_folder(db, user, public_collection):
    public_root = tempfile.mkdtemp()
    public_folder = Folder().createFolder(
        public_collection,
        "public",
        creator=user,
        parentType="collection",
        public=True,
        reuseExisting=True,
    )
    public_folder.update(dict(fsPath=public_root, isMapping=True))
    public_folder = Folder().save(public_folder)
    yield public_folder
    Folder().remove(public_folder)


@pytest.fixture
def mapped_folder(db, public_folder):
    public_root = tempfile.mkdtemp()
    public_folder.update(dict(fsPath=public_root, isMapping=True))
    mapped_folder = Folder().save(public_folder)
    yield mapped_folder
    mapped_folder.pop("fsPath")
    mapped_folder.pop("isMapping")
    shutil.rmtree(public_root)


def test_vo_methods(mapped_folder):
    from virtual_resources.rest import VirtualObject as vo

    root_path = pathlib.Path(mapped_folder["fsPath"])
    non_existing = root_path / "something"

    with pytest.raises(ValidationException):
        vo().is_file(non_existing, mapped_folder["_id"])

    with pytest.raises(ValidationException):
        vo().is_dir(non_existing, mapped_folder["_id"])


@pytest.mark.plugin("virtual_resources")
def test_mapping_creation(server, admin, user, public_folder):
    resp = server.request(
        path="/folder/{_id}".format(**public_folder),
        method="PUT",
        user=user,
        params={"fsPath": "/etc", "isMapping": True},  # H4ck3r detected!
    )
    assertStatus(resp, 403)
    assert resp.json == {
        "message": "Must be admin to setup virtual folders.",
        "type": "access",
    }

    resp = server.request(
        path="/folder/{_id}".format(**public_folder),
        method="PUT",
        user=admin,
        params={"fsPath": "/etc", "isMapping": True},  # G0d
    )
    assertStatusOk(resp)
    assert "fsPath" in resp.json
    assert "isMapping" in resp.json
    assert resp.json["fsPath"] == "/etc"
    assert resp.json["isMapping"] is True
