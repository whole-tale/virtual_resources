#!/usr/bin/env python
# -*- coding: utf-8 -*-

import pathlib
import pytest
import random
import string

from girder.exceptions import ValidationException
from pytest_girder.assertions import assertStatusOk, assertStatus

chunk1, chunk2 = ("hello ", "world")
chunkData = chunk1.encode("utf8") + chunk2.encode("utf8")


def random_string(length=10):
    """Generate a random string of fixed length."""
    letters = string.ascii_lowercase
    return "".join(random.choice(letters) for i in range(length))


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
