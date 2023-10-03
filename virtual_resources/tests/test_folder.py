#!usr/bin/env python
# -*- coding: utf-8 -*-
import io
import pathlib
import shutil
import zipfile

import pytest

from pytest_girder.assertions import assertStatus, assertStatusOk
from pytest_girder.utils import getResponseBody

from virtual_resources.rest import VirtualObject


@pytest.mark.plugin("virtual_resources")
def test_basic_folder_ops(server, user, mapped_folder):
    public_folder = mapped_folder
    public_root = mapped_folder["fsPath"]
    resp = server.request(
        path="/folder",
        method="POST",
        user=user,
        params={
            "parentType": "folder",
            "parentId": public_folder["_id"],
            "name": "test_folder",
        },
    )
    assertStatusOk(resp)
    folder = resp.json

    actual_folder_path = pathlib.Path(public_root) / folder["name"]
    assert actual_folder_path.is_dir()

    decoded_path, decoded_root_id = VirtualObject.path_from_id(folder["_id"])
    assert decoded_path == actual_folder_path
    assert decoded_root_id == str(public_folder["_id"])

    resp = server.request(
        path="/folder",
        method="GET",
        user=user,
        params={"parentType": "folder", "parentId": str(public_folder["_id"])},
    )
    assertStatusOk(resp)
    get_folders = resp.json
    assert len(get_folders) == 1
    assert get_folders[0] == folder

    resp = server.request(
        path="/folder/{_id}".format(**folder),
        method="GET",
        user=user,
    )
    assertStatusOk(resp)
    assert resp.json == get_folders[0]

    resp = server.request(
        path="/folder/{_id}".format(**folder),
        method="PUT",
        user=user,
        params={"name": "renamed"},
    )
    assertStatusOk(resp)
    folder = resp.json
    assert not actual_folder_path.exists()
    actual_folder_path = pathlib.Path(public_root) / folder["name"]
    assert actual_folder_path.is_dir()

    resp = server.request(
        path="/folder/{_id}".format(**folder),
        method="DELETE",
        user=user,
    )
    assertStatusOk(resp)
    assert not actual_folder_path.exists()


@pytest.mark.plugin("virtual_resources")
def test_folder_move(server, user, mapped_folder, mapped_priv_folder):
    root_path = pathlib.Path(mapped_folder["fsPath"])
    dir1 = root_path / "some_dir"
    dir1.mkdir()
    file1 = dir1 / "some_file"
    with file1.open(mode="wb") as fp:
        fp.write(b"\n")

    folder_id = VirtualObject.generate_id(dir1, mapped_folder["_id"])

    resp = server.request(
        path="/folder/{}".format(folder_id),
        method="PUT",
        user=user,
        params={"name": dir1.name},
        exception=True,
    )
    assertStatus(resp, 400)
    assert (
        resp.json["message"] == "A folder or file with that name already exists here."
    )

    new_root_path = pathlib.Path(mapped_priv_folder["fsPath"])
    dir2 = new_root_path / "level1"
    dir2.mkdir()
    new_folder_id = VirtualObject.generate_id(dir2, mapped_priv_folder["_id"])

    resp = server.request(
        path="/folder/{}".format(folder_id),
        method="PUT",
        user=user,
        params={"parentId": new_folder_id, "parentType": "folder"},
    )
    assertStatusOk(resp)
    assert not dir1.exists()
    assert (dir2 / dir1.name).exists()
    new_file = dir2 / dir1.name / file1.name
    assert new_file.exists()
    new_file.unlink()
    (dir2 / dir1.name).rmdir()
    dir2.rmdir()


@pytest.mark.plugin("virtual_resources")
def test_move_to_root(server, user, mapped_folder, mapped_priv_folder):
    public_folder = mapped_folder
    root_path = pathlib.Path(public_folder["fsPath"])
    dir1 = root_path / "some_dir"
    dir1.mkdir()
    file1 = dir1 / "some_file"
    with file1.open(mode="wb") as fp:
        fp.write(b"\n")
    folder_id = VirtualObject.generate_id(dir1, public_folder["_id"])

    resp = server.request(
        path="/folder/{}".format(folder_id),
        method="PUT",
        user=user,
        params={"parentId": mapped_priv_folder["_id"], "parentType": "folder"},
    )
    assertStatusOk(resp)
    assert not dir1.exists()

    root_path = pathlib.Path(mapped_priv_folder["fsPath"])
    dir1 = root_path / "some_dir"
    file1 = dir1 / "some_file"
    assert dir1.exists()
    assert file1.exists()
    file1.unlink()
    dir1.rmdir()


@pytest.mark.plugin("virtual_resources")
def test_move_acls(server, user, extra_user, mapped_folder, mapped_priv_folder):
    root_path = pathlib.Path(mapped_folder["fsPath"])
    dir1 = root_path / "some_dir"
    dir1.mkdir()

    folder_id = VirtualObject.generate_id(dir1, mapped_folder["_id"])

    resp = server.request(
        path="/folder/{}".format(folder_id),
        method="PUT",
        user=extra_user,
        params={"parentId": mapped_priv_folder["_id"], "parentType": "folder"},
    )
    assertStatus(resp, 403)

    resp = server.request(
        path="/folder/{}".format(folder_id),
        method="PUT",
        user=user,
        params={"parentId": mapped_priv_folder["_id"], "parentType": "folder"},
    )
    assertStatusOk(resp)

    assert not dir1.exists()
    root_path = pathlib.Path(mapped_priv_folder["fsPath"])
    dir1 = root_path / "some_dir"
    assert dir1.exists()
    folder_id = VirtualObject.generate_id(dir1, mapped_priv_folder["_id"])

    resp = server.request(
        path="/folder/{}".format(folder_id),
        method="PUT",
        user=extra_user,
        params={"parentId": mapped_folder["_id"], "parentType": "folder"},
    )
    assertStatus(resp, 403)

    dir1.rmdir()


@pytest.mark.plugin("virtual_resources")
def test_folder_details(server, user, example_mapped_folder):
    resp = server.request(
        path="/folder/{_id}/details".format(**example_mapped_folder["girder_root"]),
        method="GET",
        user=user,
    )
    assertStatusOk(resp)
    assert resp.json == {"nFolders": 1, "nItems": 1}


@pytest.mark.plugin("virtual_resources")
def test_folder_rootpath(server, user, mapped_folder):
    root_path = pathlib.Path(mapped_folder["fsPath"])
    nested_dir = root_path / "level0" / "level1" / "level2"
    nested_dir.mkdir(parents=True)

    folder_id = VirtualObject.generate_id(nested_dir, mapped_folder["_id"])
    resp = server.request(
        path="/folder/{}/rootpath".format(folder_id),
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


@pytest.mark.plugin("virtual_resources")
def test_folder_delete_contents(server, user, mapped_folder):
    root_path = pathlib.Path(mapped_folder["fsPath"])
    nested_dir = root_path / "lone_survivor"
    nested_dir.mkdir(parents=True)
    folder_id = VirtualObject.generate_id(nested_dir, mapped_folder["_id"])

    dir1 = nested_dir / "subfolder"
    dir1.mkdir()
    file1 = dir1 / "some_file.txt"
    with file1.open(mode="wb") as fp:
        fp.write(b"file1\n")
    file2 = nested_dir / "other_file.txt"
    with file2.open(mode="wb") as fp:
        fp.write(b"file2\n")
    assert len(list(nested_dir.iterdir())) == 2

    resp = server.request(
        path="/folder/{}/contents".format(folder_id),
        method="DELETE",
        user=user,
    )
    assertStatusOk(resp)
    assert len(list(nested_dir.iterdir())) == 0
    nested_dir.rmdir()


@pytest.mark.plugin("virtual_resources")
def test_folder_download(server, user, mapped_folder):
    root_path = pathlib.Path(mapped_folder["fsPath"])
    nested_dir = root_path / "lone_survivor"
    nested_dir.mkdir(parents=True)
    folder_id = VirtualObject.generate_id(nested_dir, mapped_folder["_id"])

    dir1 = nested_dir / "subfolder"
    dir1.mkdir()
    file1 = dir1 / "some_file.txt"
    with file1.open(mode="wb") as fp:
        fp.write(b"file1\n")
    file2 = nested_dir / "other_file.txt"
    with file2.open(mode="wb") as fp:
        fp.write(b"file2\n")
    assert len(list(nested_dir.iterdir())) == 2

    resp = server.request(
        path="/folder/{}/download".format(folder_id),
        method="GET",
        user=user,
        isJson=False,
    )
    assertStatusOk(resp)
    with zipfile.ZipFile(io.BytesIO(getResponseBody(resp, text=False)), "r") as fp:
        assert sorted(fp.namelist()) == ["other_file.txt", "subfolder/some_file.txt"]
        # TODO should probably check the content too...


@pytest.mark.plugin("virtual_resources")
def test_folder_copy(server, user, private_folder, mapped_folder, extra_user):
    root_path = pathlib.Path(mapped_folder["fsPath"])
    dir1 = root_path / "source_folder"
    dir1.mkdir(parents=True)
    folder_id = VirtualObject.generate_id(dir1, mapped_folder["_id"])
    file1 = dir1 / "file.dat"
    with file1.open(mode="wb") as fp:
        fp.write(b"file1\n")

    resp = server.request(
        path="/folder/{}/copy".format(mapped_folder["_id"]),
        method="POST",
        user=user,
        params={"name": "new_copy"},
        exception=True,
    )
    assertStatus(resp, 500)
    assert resp.json["message"] == "Copying mappings is not allowed."

    resp = server.request(
        path="/folder/{}/copy".format(folder_id),
        method="POST",
        user=extra_user,
        params={
            "name": "new_copy",
            "parentId": str(private_folder["_id"]),
            "parentType": "folder",
        },
    )
    assertStatus(resp, 403)

    resp = server.request(
        path="/folder/{}/copy".format(folder_id),
        method="POST",
        user=user,
        params={
            "name": "new_copy",
            "parentId": str(private_folder["_id"]),
            "parentType": "folder",
        },
        exception=True,
    )
    assertStatus(resp, 500)
    assert resp.json["message"] == "Folder {} is not a mapping.".format(
        private_folder["_id"]
    )

    resp = server.request(
        path="/folder/{}/copy".format(folder_id),
        method="POST",
        user=user,
        params={"name": "new_copy"},
    )
    assertStatus(resp, 200)
    assert (dir1.with_name("new_copy") / file1.name).is_file()

    resp = server.request(
        path="/folder/{}/copy".format(folder_id),
        method="POST",
        user=user,
        params={},
        exception=True,
    )
    assertStatus(resp, 200)
    new_folder = resp.json
    assert new_folder["name"] == "source_folder (1)"
    assert (dir1.with_name("source_folder (1)") / file1.name).is_file()

    resp = server.request(
        path="/folder/{}/copy".format(folder_id),
        method="POST",
        user=user,
        params={
            "name": "copy_within_copy",
            "parentId": new_folder["_id"],
            "parentType": "folder",
        },
    )
    assertStatusOk(resp)
    assert (
        dir1.with_name("source_folder (1)") / "copy_within_copy" / file1.name
    ).is_file()


@pytest.mark.plugin("virtual_resources")
def test_exists_already(server, user, mapped_folder):
    public_folder = mapped_folder
    root_path = pathlib.Path(public_folder["fsPath"])
    some_dir = root_path / "some_folder"
    some_dir.mkdir(parents=True)

    resp = server.request(
        path="/folder",
        method="POST",
        user=user,
        params={
            "parentType": "folder",
            "parentId": public_folder["_id"],
            "name": "some_folder",
        },
    )
    assertStatus(resp, 400)
    folder = resp.json
    assert folder == {
        "type": "validation",
        "message": "A folder with that name already exists here.",
        "field": "name",
    }
    some_dir.rmdir()


@pytest.mark.plugin("virtual_resources")
def test_folder_listing(server, user, mapped_folder):
    public_folder = mapped_folder
    root_path = pathlib.Path(public_folder["fsPath"])
    some_dir = root_path / "some_folder_with_subfolders"
    some_dir.mkdir(parents=True)

    (some_dir / "subfolder1").mkdir()
    (some_dir / "subfolder2").mkdir()

    resp = server.request(
        path="/folder",
        method="GET",
        user=user,
        params={"parentType": "folder", "parentId": str(public_folder["_id"])},
    )
    assertStatusOk(resp)
    some_dir_obj = resp.json[0]
    assert some_dir_obj["name"] == "some_folder_with_subfolders"

    resp = server.request(
        path="/folder",
        method="GET",
        user=user,
        params={"parentType": "folder", "parentId": some_dir_obj["_id"]},
    )
    assertStatusOk(resp)
    assert len(resp.json) == 2

    resp = server.request(
        path="/folder",
        method="GET",
        user=user,
        params={
            "parentType": "folder",
            "parentId": some_dir_obj["_id"],
            "name": "subfolder2",
        },
    )
    assertStatusOk(resp)
    assert len(resp.json) == 1
    assert resp.json[0]["name"] == "subfolder2"

    resp = server.request(
        path="/folder",
        method="GET",
        user=user,
        params={
            "parentType": "folder",
            "parentId": some_dir_obj["_id"],
            "name": "nope",
        },
    )
    assertStatusOk(resp)
    assert len(resp.json) == 0
    shutil.rmtree(some_dir.as_posix())
