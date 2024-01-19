#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import pathlib

from girder.models.folder import Folder

import pytest

from pytest_girder.assertions import assertStatus, assertStatusOk

from girder_virtual_resources.rest import VirtualObject


@pytest.mark.plugin("girder_virtual_resources")
def test_resource_copy(server, admin, example_mapped_folder):
    mapped_folder = example_mapped_folder["girder_root"]
    nested_dir = example_mapped_folder["nested_dir"]
    file1 = example_mapped_folder["file1"]
    file1_contents = example_mapped_folder["file1_contents"]
    file2 = example_mapped_folder["file2"]
    file2_contents = example_mapped_folder["file2_contents"]

    root_path = pathlib.Path(mapped_folder["fsPath"])
    copy_target_dir = root_path / "copy_dest"
    copy_target_dir.mkdir()

    resources = {
        "item": [VirtualObject.generate_id(file1.as_posix(), mapped_folder["_id"])],
        "folder": [
            VirtualObject.generate_id(nested_dir.as_posix(), mapped_folder["_id"])
        ],
    }

    parentId = VirtualObject.generate_id(
        copy_target_dir.as_posix(), mapped_folder["_id"]
    )

    resp = server.request(
        path="/resource/copy",
        method="POST",
        user=admin,
        params={
            "parentType": "folder",
            "parentId": parentId,
            "resources": json.dumps(resources),
        },
    )
    assertStatusOk(resp)

    copied_dir = copy_target_dir / nested_dir.name
    assert copied_dir.is_dir()
    copied_file1 = copy_target_dir / file1.name
    assert copied_file1.is_file()
    with copied_file1.open(mode="rb") as fp:
        assert fp.read() == file1_contents
    copied_file2 = copied_dir / file2.name
    assert copied_file2.is_file()
    with copied_file2.open(mode="rb") as fp:
        assert fp.read() == file2_contents


@pytest.mark.plugin("girder_virtual_resources")
def test_resource_move(server, admin, example_mapped_folder):
    mapped_folder = example_mapped_folder["girder_root"]
    nested_dir = example_mapped_folder["nested_dir"]
    file1 = example_mapped_folder["file1"]
    file1_contents = example_mapped_folder["file1_contents"]
    file2 = example_mapped_folder["file2"]
    file2_contents = example_mapped_folder["file2_contents"]

    root_path = pathlib.Path(mapped_folder["fsPath"])
    # MOVE
    move_target_dir = root_path / "moved"
    move_target_dir.mkdir()
    resources = {
        "item": [
            VirtualObject.generate_id(file1.as_posix(), mapped_folder["_id"]),
            VirtualObject.generate_id(file2.as_posix(), mapped_folder["_id"]),
        ],
        "folder": [
            VirtualObject.generate_id(nested_dir.as_posix(), mapped_folder["_id"])
        ],
    }
    parentId = VirtualObject.generate_id(
        move_target_dir.as_posix(), mapped_folder["_id"]
    )
    resp = server.request(
        path="/resource/move",
        method="PUT",
        user=admin,
        params={
            "parentType": "folder",
            "parentId": parentId,
            "resources": json.dumps(resources),
        },
    )
    assertStatusOk(resp)
    for resource in (nested_dir, file1, file2):
        assert not resource.exists()

    moved_dir = move_target_dir / nested_dir.name
    moved_file1 = move_target_dir / file1.name
    moved_file2 = move_target_dir / file2.name
    assert moved_dir.is_dir()
    assert moved_file1.is_file()
    with open(moved_file1, "rb") as fp:
        assert fp.read() == file1_contents
    assert moved_file2.is_file()
    with open(moved_file2, "rb") as fp:
        assert fp.read() == file2_contents


@pytest.mark.plugin("girder_virtual_resources")
def test_resource_delete(server, admin, example_mapped_folder):
    mapped_folder = example_mapped_folder["girder_root"]
    root_path = pathlib.Path(mapped_folder["fsPath"])
    resources = {"item": [], "folder": [str(mapped_folder["_id"])]}
    for path in root_path.iterdir():
        if path.is_dir():
            key = "folder"
        else:
            key = "item"
        resources[key].append(
            VirtualObject.generate_id(path.as_posix(), mapped_folder["_id"])
        )

    resp = server.request(
        path="/resource",
        method="DELETE",
        user=admin,
        params={"resources": json.dumps(resources)},
    )
    assertStatusOk(resp)
    assert len(list(root_path.iterdir())) == 0
    mapped_folder = Folder().load(mapped_folder["_id"], force=True)
    assert mapped_folder is None


@pytest.mark.plugin("girder_virtual_resources")
def test_lookup(server, admin, user, example_mapped_folder):
    mapped_folder = example_mapped_folder["girder_root"]

    for model, lookup_path in (
        ("folder", "/collection/test_collection/public/level0"),
        ("item", "/collection/test_collection/public/some_file.txt"),
    ):
        resp = server.request(
            path="/resource/lookup",
            method="GET",
            user=None,
            params={"path": lookup_path},
        )
        assertStatusOk(resp)
        assert resp.json["_modelType"] == model
        assert resp.json["name"] == pathlib.Path(lookup_path).name

    mapped_folder["public"] = False
    mapped_folder = Folder().save(mapped_folder)
    for lookup_path in (
        "/collection/test_collection/public/level0",
        "/collection/test_collection/public/some_file.txt",
    ):
        resp = server.request(
            path="/resource/lookup",
            method="GET",
            user=None,
            params={"path": lookup_path},
        )
        assertStatus(resp, 400)
        assert "Path not found" in resp.json["message"]
        assert resp.json["type"] == "validation"

    # test should return empty document for nonexisting file
    for path in (
        "/user/nonexisting/blah",
        "/collection/test_collection/public/level0/blah",
    ):
        resp = server.request(
            path="/resource/lookup",
            method="GET",
            user=admin,
            params={"path": path, "test": True},
            exception=True,
        )
        assertStatusOk(resp)
        assert resp.json is None

    for path, msg in (
        ("/user/nonexisting/blah", "User not found: nonexisting"),
        ("/collection/nonexisting/blah", "Collection not found: nonexisting"),
        ("/blah/nonexisting/blah", "Invalid path format"),
        (
            "/collection/test_collection/public/level0/blah",
            "Path not found: collection/test_collection/public/level0/blah",
        ),
    ):
        resp = server.request(
            path="/resource/lookup",
            method="GET",
            user=admin,
            params={"path": path},
            exception=True,
        )
        assertStatus(resp, 400)
        assert resp.json["message"] == msg


@pytest.mark.plugin("girder_virtual_resources")
def test_copy_existing_name(example_mapped_folder, server, user):
    mapped_folder = example_mapped_folder["girder_root"]
    file1 = example_mapped_folder["file1"]
    file1_contents = example_mapped_folder["file1_contents"]

    resources = {
        "item": [VirtualObject.generate_id(file1.as_posix(), mapped_folder["_id"])]
    }
    resp = server.request(
        path="/resource/copy",
        method="POST",
        user=user,
        params={
            "parentType": "folder",
            "parentId": mapped_folder["_id"],
            "resources": json.dumps(resources),
        },
    )
    assertStatusOk(resp)
    new_file = file1.parent / f"{file1.name} (1)"
    assert new_file.is_file()
    with open(new_file, "rb") as fp:
        assert fp.read() == file1_contents
    new_file.unlink()


@pytest.mark.plugin("girder_virtual_resources")
def test_path(server, user, example_mapped_folder):
    mapped_folder = example_mapped_folder["girder_root"]
    nested_dir = example_mapped_folder["nested_dir"]
    file2 = example_mapped_folder["file2"]

    item_id = VirtualObject.generate_id(file2.as_posix(), mapped_folder["_id"])
    folder_id = VirtualObject.generate_id(nested_dir.as_posix(), mapped_folder["_id"])

    resp = server.request(
        path="/resource/{}/path".format(item_id),
        method="GET",
        user=user,
        params={"type": "item"},
    )
    assertStatusOk(resp)
    assert (
        resp.json
        == f"/collection/test_collection/{mapped_folder['name']}/{nested_dir.name}/{file2.name}"
    )

    resp = server.request(
        path="/resource/{}/path".format(folder_id),
        method="GET",
        user=user,
        params={"type": "folder"},
    )
    assertStatusOk(resp)
    assert (
        resp.json
        == f"/collection/test_collection/{mapped_folder['name']}/{nested_dir.name}"
    )

    for res_id, res_type in (
        (item_id, "folder"),
        (folder_id, "item"),
        (item_id, "collection"),
    ):
        resp = server.request(
            path="/resource/{}/path".format(res_id),
            method="GET",
            user=user,
            params={"type": res_type},
            exception=True,
        )
        assertStatus(resp, 400)
        assert resp.json["message"] == "Invalid resource id."
