#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import pathlib
import shutil

import pytest
from girder.models.assetstore import Assetstore
from girder.models.setting import Setting
from girder.settings import SettingKey
from pytest_girder.assertions import assertStatus, assertStatusOk
from pytest_girder.utils import getResponseBody

from girder_virtual_resources.rest import VirtualObject

chunk1, chunk2 = ("hello ", "world")


@pytest.mark.plugin("girder_virtual_resources")
def test_basic_file_ops(server, user, extra_user, example_mapped_folder):
    mapped_folder = example_mapped_folder["girder_root"]
    file2 = example_mapped_folder["file2"]
    file2_contents = example_mapped_folder["file2_contents"]

    resp = server.request(
        path="/file/{}".format(VirtualObject.generate_id(file2.as_posix(), mapped_folder["_id"])),
        method="GET",
        user=user,
    )
    assertStatusOk(resp)
    fobj = resp.json
    assert fobj["name"] == file2.name
    assert int(fobj["size"]) == file2.stat().st_size

    resp = server.request(
        path="/file/{_id}".format(**fobj),
        method="PUT",
        user=extra_user,
        params={"name": "new_name"},
    )
    assertStatus(resp, 403)

    resp = server.request(
        path="/file/{_id}".format(**fobj),
        method="PUT",
        user=user,
        params={"name": "new_name"},
    )
    assertStatusOk(resp)
    fobj = resp.json

    assert not file2.exists()
    file2 = file2.with_name(fobj["name"])
    assert file2.exists() and file2.is_file()

    resp = server.request(
        path="/file/{_id}/download".format(**fobj),
        method="GET",
        user=extra_user,
        isJson=False,
    )
    assertStatusOk(resp)

    contentDisposition = 'filename="%s"' % fobj["name"]
    assert resp.headers["Content-Type"] == "application/octet-stream"
    assert resp.headers["Content-Disposition"] == "attachment; %s" % contentDisposition
    assert file2_contents.decode() == getResponseBody(resp)

    # Test downloading the file with contentDisposition=inline.
    params = {"contentDisposition": "inline"}
    resp = server.request(
        path="/file/{_id}/download".format(**fobj),
        method="GET",
        user=extra_user,
        isJson=False,
        params=params,
    )
    assertStatusOk(resp)
    assert resp.headers["Content-Type"] == "application/octet-stream"
    assert resp.headers["Content-Disposition"] == "inline; %s" % contentDisposition
    assert file2_contents.decode() == getResponseBody(resp)

    # Test downloading with an offset
    resp = server.request(
        path="/file/{_id}/download".format(**fobj),
        method="GET",
        user=extra_user,
        isJson=False,
        params={"offset": 1},
    )
    assertStatus(resp, 206)
    assert file2_contents[1:].decode() == getResponseBody(resp)

    # Test downloading with a range header and query range params
    respHeader = server.request(
        path="/file/{_id}/download".format(**fobj),
        method="GET",
        user=extra_user,
        isJson=False,
        additionalHeaders=[("Range", "bytes=2-7")],
    )
    respQuery = server.request(
        path="/file/{_id}/download".format(**fobj),
        method="GET",
        user=extra_user,
        isJson=False,
        params={"offset": 2, "endByte": 8},
    )
    for resp in [respHeader, respQuery]:
        assert file2_contents[2:8].decode() == getResponseBody(resp)
        assert resp.headers["Accept-Ranges"] == "bytes"
        length = len(file2_contents)
        begin, end = min(length, 2), min(length, 8)
        assert resp.headers["Content-Length"] == end - begin
        if length:
            assertStatus(resp, 206)
            assert resp.headers["Content-Range"] == "bytes %d-%d/%d" % (
                begin,
                end - 1,
                length,
            )
        else:
            assertStatusOk(resp)

    resp = server.request(
        path="/file/{_id}".format(**fobj),
        method="DELETE",
        user=extra_user,
        isJson=False,
    )
    assertStatus(resp, 403)
    assert file2.exists()

    resp = server.request(
        path="/file/{_id}".format(**fobj),
        method="DELETE",
        user=user,
        isJson=False,
    )
    assertStatusOk(resp)
    assert not file2.exists()


@pytest.mark.plugin("girder_virtual_resources")
def test_upload_file(server, user, extra_user, fsAssetstore, mapped_priv_folder):
    """
    Uploads a non-empty file to the server.
    """
    name = "test_file.txt"
    # Initialize the upload
    resp = server.request(
        path="/file",
        method="POST",
        user=user,
        params={
            "parentType": "folder",
            "parentId": mapped_priv_folder["_id"],
            "name": name,
            "size": len(chunk1) + len(chunk2),
            "mimeType": "text/plain",
        },
    )
    assertStatusOk(resp)

    uploadId = resp.json["_id"]

    # Uploading with no user should fail
    resp = server.request(
        path="/file/chunk",
        method="POST",
        user=None,
        body=chunk1,
        params={"uploadId": uploadId, "offset": 0},
        type="plain/text",
    )
    assertStatus(resp, 401)

    # Uploading with the wrong user should fail
    resp = server.request(
        path="/file/chunk",
        method="POST",
        user=extra_user,
        body=chunk1,
        params={"uploadId": uploadId, "offset": 0},
        type="plain/text",
    )
    assertStatus(resp, 403)

    # Sending the first chunk should fail because the default minimum chunk
    # size is larger than our chunk.
    Setting().unset(SettingKey.UPLOAD_MINIMUM_CHUNK_SIZE)
    resp = server.request(
        path="/file/chunk",
        method="POST",
        user=user,
        body=chunk1,
        params={"uploadId": uploadId, "offset": 0},
        type="plain/text",
    )
    assertStatus(resp, 400)
    assert resp.json == {
        "type": "validation",
        "message": "Chunk is smaller than the minimum size.",
    }

    # Send the first chunk (use multipart)
    Setting().set(SettingKey.UPLOAD_MINIMUM_CHUNK_SIZE, 0)
    resp = server.request(
        path="/file/chunk",
        method="POST",
        user=user,
        body=chunk1,
        params={"uploadId": uploadId, "offset": 0},
        type="plain/text",
    )
    assertStatusOk(resp)

    # Attempting to send second chunk with incorrect offset should fail
    resp = server.request(
        path="/file/chunk",
        method="POST",
        user=user,
        body=chunk2,
        params={"uploadId": uploadId, "offset": 0},
        type="plain/text",
    )
    assertStatus(resp, 400)

    # Ask for completion before sending second chunk should fail
    resp = server.request(
        path="/file/completion",
        method="POST",
        user=user,
        params={"uploadId": uploadId},
    )
    assertStatus(resp, 400)

    # Request offset from server (simulate a resume event)
    resp = server.request(path="/file/offset", user=user, params={"uploadId": uploadId})
    assertStatusOk(resp)

    # Trying to send too many bytes should fail
    current_offset = resp.json["offset"]
    resp = server.request(
        path="/file/chunk",
        method="POST",
        user=user,
        body=f"extra_{chunk2}_bytes",
        params={"uploadId": uploadId, "offset": current_offset},
        type="plain/text",
    )
    assertStatus(resp, 400)
    assert resp.json == {"type": "validation", "message": "Received too many bytes."}

    # The offset should not have changed
    resp = server.request(path="/file/offset", user=user, params={"uploadId": uploadId})
    assertStatusOk(resp)
    assert resp.json["offset"], current_offset

    # Now upload the second chunk (using query params + body)
    resp = server.request(
        path="/file/chunk",
        method="POST",
        user=user,
        body=chunk2,
        params={"offset": resp.json["offset"], "uploadId": uploadId},
        type="text/plain",
    )
    assertStatusOk(resp)

    file = resp.json

    assert "itemId" in file
    assert file["name"] == name
    assert file["size"] == len(chunk1 + chunk2)


@pytest.mark.plugin("girder_virtual_resources")
def test_upload_odd_cases(server, fsAssetstore, user, extra_user, mapped_priv_folder):
    # Change dest perms to ro
    dest_dir = pathlib.Path(mapped_priv_folder["fsPath"])
    dest_dir.chmod(0o551)
    resp = server.request(
        path="/file",
        method="POST",
        user=user,
        params={
            "parentType": "folder",
            "parentId": mapped_priv_folder["_id"],
            "name": "empty_file.txt",
            "size": "0",
            "mimeType": "text/plain",
        },
        exception=True,
    )
    assertStatus(resp, 500)
    assert resp.json["message"] == "Insufficient perms to write on {}".format(
        mapped_priv_folder["fsPath"]
    )

    dest_dir.chmod(0o751)
    (dest_dir / "empty_file.txt").mkdir()
    resp = server.request(
        path="/file",
        method="POST",
        user=user,
        params={
            "parentType": "folder",
            "parentId": mapped_priv_folder["_id"],
            "name": "empty_file.txt",
            "size": "0",
            "mimeType": "text/plain",
        },
        exception=True,
    )
    assertStatus(resp, 500)
    assert (
        resp.json["message"]
        == "IsADirectoryError: IsADirectoryError(21, 'Is a directory')"
    )
    (dest_dir / "empty_file.txt").rmdir()

    resp = server.request(
        path="/file",
        method="POST",
        user=user,
        params={
            "parentType": "folder",
            "parentId": mapped_priv_folder["_id"],
            "name": "empty_file.txt",
            "size": 0,
            "mimeType": "text/plain",
        },
    )
    assertStatusOk(resp)

    resp = server.request(
        path="/file",
        method="POST",
        user=user,
        params={
            "parentType": "folder",
            "parentId": mapped_priv_folder["_id"],
            "name": "blah_file.txt",
            "size": len(chunk1),
            "mimeType": "text/plain",
        },
    )
    assertStatusOk(resp)
    upload = resp.json

    (dest_dir / "blah_file.txt").unlink()
    dest_dir.chmod(0o551)
    resp = server.request(
        path="/file/chunk",
        method="POST",
        user=user,
        params={"uploadId": upload["_id"], "offset": 0},
        type="text/plain",
        body=chunk1,
        exception=True,
    )
    assertStatus(resp, 500)
    assert resp.json["message"] == "Exception: Exception('Failed to store upload.')"
    dest_dir.chmod(0o775)

    resp = server.request(
        path="/file",
        method="POST",
        user=user,
        params={
            "parentType": "folder",
            "parentId": mapped_priv_folder["_id"],
            "name": "full_body.txt",
            "size": len(chunk1),
            "mimeType": "text/plain",
        },
        type="text/plain",
        body=chunk1,
    )
    assertStatusOk(resp)
    fobj = resp.json
    assert fobj["size"] == len(chunk1)
    assert fobj["_modelType"] == "file"

    dest_dir = pathlib.Path(mapped_priv_folder["fsPath"]) / "some_dir"
    dest_dir.mkdir()
    resp = server.request(
        path="/file",
        method="POST",
        user=user,
        params={
            "parentType": "folder",
            "parentId": VirtualObject.generate_id(dest_dir.as_posix(), mapped_priv_folder["_id"]),
            "name": "full_body.txt",
            "size": len(chunk1),
            "mimeType": "text/plain",
        },
        type="text/plain",
        body=chunk1,
    )
    assertStatusOk(resp)
    fobj = resp.json
    assert fobj["size"] == len(chunk1)
    assert (dest_dir / fobj["name"]).is_file()


@pytest.mark.plugin("girder_virtual_resources")
def test_fs_assetstore(server, fsAssetstore, user, mapped_priv_folder):
    """
    Test usage of the Filesystem assetstore type.
    """
    assetstore = Assetstore().getCurrent()
    root = assetstore["root"]

    # Clean out the test assetstore on disk
    shutil.rmtree(root)

    # First clean out the temp directory
    tmpdir = os.path.join(root, "temp")
    if os.path.isdir(tmpdir):
        for tempname in os.listdir(tmpdir):
            os.remove(os.path.join(tmpdir, tempname))

    # Upload the two-chunk file
    name = "test_file.txt"
    # Initialize the upload
    resp = server.request(
        path="/file",
        method="POST",
        user=user,
        params={
            "parentType": "folder",
            "parentId": mapped_priv_folder["_id"],
            "name": name,
            "size": len(chunk1) + len(chunk2),
            "mimeType": "text/plain",
        },
    )
    assertStatusOk(resp)
    uploadId = resp.json["_id"]

    # Send the first chunk (use multipart)
    Setting().set(SettingKey.UPLOAD_MINIMUM_CHUNK_SIZE, 0)
    resp = server.request(
        path="/file/chunk",
        method="POST",
        user=user,
        body=chunk1,
        params={"uploadId": uploadId, "offset": 0},
        type="plain/text",
    )
    assertStatusOk(resp)

    # Now upload the second chunk (using query params + body)
    resp = server.request(
        path="/file/chunk",
        method="POST",
        user=user,
        body=chunk2,
        params={"offset": 6, "uploadId": uploadId},
        type="text/plain",
    )
    assertStatusOk(resp)
    file = resp.json
    assert file["_modelType"] == "file"

    # We want to make sure the file got uploaded correctly into
    # the assetstore and stored at the right location
    # assert os.stat(abspath).st_size, file["size"])
    # assert os.stat(abspath).st_mode & 0o777, DEFAULT_PERMS)

    # Make sure access control is enforced on download
    resp = server.request(path="/file/%s/download" % file["_id"], method="GET")
    assertStatus(resp, 401)

    # Make sure access control is enforced on get info
    resp = server.request(path="/file/" + str(file["_id"]), method="GET")
    assertStatus(resp, 401)

    # Make sure we can get the file info and that it's filtered
    resp = server.request(path="/file/" + str(file["_id"]), method="GET", user=user)
    assertStatusOk(resp)
    assert resp.json["mimeType"] == "application/octet-stream"  # FIXME
    assert resp.json["exts"] == ["txt"]
    assert resp.json["_modelType"] == "file"
    assert resp.json["creatorId"] == str(user["_id"])
    assert resp.json["size"] == file["size"]
    assert "itemId" in resp.json
    # assert "assetstoreId" in resp.json)  # FIXME!
    # assert not "sha512" in resp.json)  # FIXME ?
