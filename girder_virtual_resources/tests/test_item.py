#!/usr/bin/env python
# -*- coding: utf-8 -*-

import pathlib
import shutil

import pytest

from pytest_girder.assertions import assertStatus, assertStatusOk

from girder_virtual_resources.rest import VirtualObject


@pytest.mark.plugin("girder_virtual_resources")
def test_basic_item_ops(server, user, mapped_folder):
    root_path = pathlib.Path(mapped_folder["fsPath"])
    nested_dir = root_path / "level0"
    nested_dir.mkdir(parents=True)
    parentId = VirtualObject.generate_id(nested_dir.as_posix(), mapped_folder["_id"])

    resp = server.request(
        path="/item",
        method="POST",
        user=user,
        params={"folderId": parentId, "name": "test_item"},
    )
    assertStatusOk(resp)
    item = resp.json

    actual_item_path = nested_dir / item["name"]
    assert actual_item_path.is_file()

    decoded_path, decoded_root_id = VirtualObject.path_from_id(item["_id"])
    assert decoded_path == actual_item_path
    assert decoded_root_id == str(mapped_folder["_id"])

    resp = server.request(
        path="/item",
        method="GET",
        user=user,
        params={"parentType": "folder", "parentId": str(parentId), "name": "nope"},
    )
    assertStatusOk(resp)
    assert len(resp.json) == 0

    resp = server.request(
        path="/item",
        method="GET",
        user=user,
        params={
            "parentType": "folder",
            "parentId": str(parentId),
            "name": item["name"],
        },
    )
    assertStatusOk(resp)
    assert len(resp.json) == 1

    resp = server.request(
        path="/item",
        method="GET",
        user=user,
        params={"parentType": "folder", "parentId": str(parentId)},
    )
    assertStatusOk(resp)
    get_items = resp.json
    assert len(get_items) == 1
    assert get_items[0] == item

    resp = server.request(path="/item/{_id}".format(**item), method="GET", user=user)
    assertStatusOk(resp)
    assert resp.json == get_items[0]

    resp = server.request(
        path="/item/{_id}".format(**item),
        method="PUT",
        user=user,
        params={"name": "renamed"},
    )
    assertStatusOk(resp)
    item = resp.json
    assert not actual_item_path.exists()
    actual_item_path = actual_item_path.with_name(item["name"])
    assert actual_item_path.is_file()

    resp = server.request(
        path="/item/{_id}/files".format(**item),
        method="GET",
        user=user,
    )
    assertStatusOk(resp)
    files = resp.json
    assert len(files) == 1
    assert files[0]["_id"] == item["_id"]

    resp = server.request(path="/item/{_id}".format(**item), method="DELETE", user=user)
    assertStatusOk(resp)
    assert not actual_item_path.exists()
    shutil.rmtree(nested_dir.as_posix())


@pytest.mark.plugin("girder_virtual_resources")
def test_item_rootpath(server, user, mapped_folder):
    root_path = pathlib.Path(mapped_folder["fsPath"])
    nested_dir = root_path / "level0" / "level1"
    nested_dir.mkdir(parents=True)

    file1 = nested_dir / "some_file"
    file_contents = b"hello world\n"
    with file1.open(mode="wb") as fp:
        fp.write(file_contents)

    item_id = VirtualObject.generate_id(file1, mapped_folder["_id"])
    resp = server.request(
        path="/item/{}/rootpath".format(item_id),
        method="GET",
        user=user,
    )
    assertStatusOk(resp)
    assert len(resp.json) == 4
    rootpath = resp.json
    assert rootpath[0]["type"] == "collection"
    assert rootpath[0]["object"]["_id"] == str(mapped_folder["parentId"])
    assert rootpath[1]["type"] == "folder"
    assert rootpath[1]["object"]["_id"] == str(mapped_folder["_id"])
    assert rootpath[2]["type"] == "folder"
    assert rootpath[2]["object"]["name"] == "level0"
    assert rootpath[3]["type"] == "folder"
    assert rootpath[3]["object"]["name"] == "level1"

    shutil.rmtree((root_path / "level0").as_posix())


@pytest.mark.plugin("girder_virtual_resources")
def test_copy_item(server, user, mapped_folder, mapped_priv_folder):
    root_path = pathlib.Path(mapped_folder["fsPath"])
    file1 = root_path / "some_file"
    file_contents = b"hello world\n"
    with file1.open(mode="wb") as fp:
        fp.write(file_contents)
    item_id = VirtualObject.generate_id(file1, mapped_folder["_id"])

    # Copy in the same folder
    resp = server.request(
        path="/item/{}/copy".format(item_id),
        method="POST",
        user=user,
        params={"name": "copy"},
    )
    assertStatusOk(resp)

    assert file1.with_name("copy").is_file()
    with open(file1.with_name("copy").as_posix(), "rb") as fp:
        assert fp.read() == file_contents

    # Copy in the same mapping but different folder
    new_dir = root_path / "some_dir"
    new_dir.mkdir()
    folder_id = VirtualObject.generate_id(new_dir, mapped_folder["_id"])
    resp = server.request(
        path="/item/{}/copy".format(item_id),
        method="POST",
        user=user,
        params={"folderId": str(folder_id)},
    )
    assertStatusOk(resp)
    new_file = new_dir / "some_file"
    assert new_file.is_file()
    with open(new_file.as_posix(), "rb") as fp:
        assert fp.read() == file_contents

    # Copy between two mappings
    resp = server.request(
        path="/item/{}/copy".format(item_id),
        method="POST",
        user=user,
        params={"folderId": str(mapped_priv_folder["_id"])},
    )
    assertStatusOk(resp)
    cors_file = pathlib.Path(mapped_priv_folder["fsPath"]) / "some_file"
    assert cors_file.is_file()
    with open(cors_file.as_posix(), "rb") as fp:
        assert fp.read() == file_contents


@pytest.mark.plugin("girder_virtual_resources")
def test_copy_item_to_nonmapping(server, user, public_folder, mapped_priv_folder):
    root_path = pathlib.Path(mapped_priv_folder["fsPath"])
    file1 = root_path / "some_file"
    file_contents = b"hello world\n"
    with file1.open(mode="wb") as fp:
        fp.write(file_contents)
    item_id = VirtualObject.generate_id(file1, mapped_priv_folder["_id"])
    # Try to copy to non mapping
    resp = server.request(
        path="/item/{}/copy".format(item_id),
        method="POST",
        user=user,
        params={"folderId": str(public_folder["_id"])},
        exception=True,
    )
    assertStatus(resp, 500)
    assert resp.json["message"] == "Folder {} is not a mapping.".format(
        public_folder["_id"]
    )


@pytest.mark.plugin("girder_virtual_resources")
def test_move_item(
    server, user, mapped_folder, mapped_priv_folder, extra_public_folder
):
    root_path = pathlib.Path(mapped_folder["fsPath"])
    subdir = root_path / "subdir"
    subdir.mkdir()
    file1 = subdir / "to_be_moved"
    file_contents = b"hello world asdfadsf\n"
    with file1.open(mode="wb") as fp:
        fp.write(file_contents)
    folder_id = VirtualObject.generate_id(subdir, mapped_folder["_id"])
    item_id = VirtualObject.generate_id(file1, mapped_folder["_id"])

    # Move with the same name (400)
    resp = server.request(
        path="/item/{}".format(item_id),
        method="PUT",
        user=user,
        params={"name": file1.name, "folderId": folder_id},
    )
    assertStatus(resp, 400)
    assert (
        resp.json["message"] == "A folder or file with that name already exists here."
    )

    # Move within the same folder
    resp = server.request(
        path="/item/{}".format(item_id),
        method="PUT",
        user=user,
        params={"name": "after_move", "folderId": folder_id},
    )
    assertStatusOk(resp)
    assert (subdir / "after_move").exists()
    assert not file1.exists()

    # Move to a different folder
    file1 = subdir / "after_move"
    item_id = VirtualObject.generate_id(file1, mapped_folder["_id"])
    resp = server.request(
        path="/item/{}".format(item_id),
        method="PUT",
        user=user,
        params={"name": "after_move", "folderId": mapped_priv_folder["_id"]},
    )
    assertStatusOk(resp)
    assert not file1.exists()
    root_path = pathlib.Path(mapped_priv_folder["fsPath"])
    file_new = root_path / "after_move"
    item_id = VirtualObject.generate_id(file_new, mapped_priv_folder["_id"])

    assert file_new.is_file()
    with open(file_new.as_posix(), "rb") as fp:
        assert fp.read() == file_contents

    # Try to move not into mapping and fail
    resp = server.request(
        path="/item/{}".format(item_id),
        method="PUT",
        user=user,
        params={
            "name": "after_move",
            "folderId": str(extra_public_folder["_id"]),
            "parentType": "folder",
        },
        exception=True,
    )
    assertStatus(resp, 500)
    assert resp.json["message"] == "Folder {} is not a mapping.".format(
        extra_public_folder["_id"]
    )

    # move it back to subdir in a mapping
    resp = server.request(
        path="/item/{}".format(item_id),
        method="PUT",
        user=user,
        params={"name": "final_move", "folderId": folder_id},
    )
    assertStatusOk(resp)
    assert not file_new.exists()
    file_new = subdir / "final_move"
    assert file_new.exists()
    file_new.unlink()


@pytest.mark.plugin("girder_virtual_resources")
def test_copy_existing_name(server, user, mapped_priv_folder):
    root_path = pathlib.Path(mapped_priv_folder["fsPath"])
    file1 = root_path / "existing.txt"
    file1_contents = b"Blah Blah Blah"
    with file1.open(mode="wb") as fp:
        fp.write(file1_contents)
    item_id = VirtualObject.generate_id(file1, mapped_priv_folder["_id"])

    resp = server.request(
        path="/item/{}/copy".format(item_id),
        method="POST",
        user=user,
        params={},
    )
    assertStatusOk(resp)
    assert resp.json["name"] == "existing.txt (1)"

    item = resp.json
    resp = server.request(
        path="/item",
        method="POST",
        user=user,
        params={"folderId": item["folderId"], "name": item["name"]},
    )
    assertStatus(resp, 400)
    folder = resp.json
    assert folder == {
        "type": "validation",
        "message": "An item with that name already exists here.",
        "field": "name",
    }

    assert (root_path / "existing.txt (1)").is_file()
    file1.unlink()
    (root_path / "existing.txt (1)").unlink()
