#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import pathlib
import random
import shutil
import string
import tempfile

from tests import base

from girder.constants import SettingKey
from girder.models.assetstore import Assetstore
from girder.models.collection import Collection
from girder.models.folder import Folder
from girder.models.user import User
from girder.models.setting import Setting

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


class FileOperationsTestCase(base.TestCase):
    def setUp(self):
        super(FileOperationsTestCase, self).setUp()
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

    def test_basic_file_ops(self):
        from girder.plugins.virtual_resources.rest import VirtualObject as vo

        root_path = pathlib.Path(self.public_folder["fsPath"])
        dir1 = root_path / "some_dir"
        dir1.mkdir()
        file1 = dir1 / "some_file"
        file_contents = b"hello world\n"
        with file1.open(mode="wb") as fp:
            fp.write(file_contents)

        resp = self.request(
            path="/file/{}".format(
                vo.generate_id(file1.as_posix(), self.public_folder["_id"])
            ),
            method="GET",
            user=self.users["sally"],
        )
        self.assertStatusOk(resp)
        fobj = resp.json
        self.assertEqual(fobj["name"], "some_file")
        self.assertEqual(int(fobj["size"]), file1.stat().st_size)

        resp = self.request(
            path="/file/{_id}".format(**fobj),
            method="PUT",
            user=self.users["joel"],
            params={"name": "new_name"},
        )
        self.assertStatus(resp, 403)

        resp = self.request(
            path="/file/{_id}".format(**fobj),
            method="PUT",
            user=self.users["sally"],
            params={"name": "new_name"},
        )
        self.assertStatusOk(resp)
        fobj = resp.json

        self.assertFalse(file1.exists())
        file1 = file1.with_name(fobj["name"])
        self.assertTrue(file1.exists() and file1.is_file())

        resp = self.request(
            path="/file/{_id}/download".format(**fobj),
            method="GET",
            user=self.users["joel"],
            isJson=False,
        )
        self.assertStatusOk(resp)

        contentDisposition = 'filename="%s"' % fobj["name"]
        self.assertEqual(resp.headers["Content-Type"], "application/octet-stream")
        self.assertEqual(
            resp.headers["Content-Disposition"], "attachment; %s" % contentDisposition
        )
        self.assertEqual(file_contents.decode(), self.getBody(resp))

        # Test downloading the file with contentDisposition=inline.
        params = {"contentDisposition": "inline"}
        resp = self.request(
            path="/file/{_id}/download".format(**fobj),
            method="GET",
            user=self.users["joel"],
            isJson=False,
            params=params,
        )
        self.assertStatusOk(resp)
        self.assertEqual(resp.headers["Content-Type"], "application/octet-stream")
        self.assertEqual(
            resp.headers["Content-Disposition"], "inline; %s" % contentDisposition
        )
        self.assertEqual(file_contents.decode(), self.getBody(resp))

        # Test downloading with an offset
        resp = self.request(
            path="/file/{_id}/download".format(**fobj),
            method="GET",
            user=self.users["joel"],
            isJson=False,
            params={"offset": 1},
        )
        self.assertStatus(resp, 206)
        self.assertEqual(file_contents[1:].decode(), self.getBody(resp))

        # Test downloading with a range header and query range params
        respHeader = self.request(
            path="/file/{_id}/download".format(**fobj),
            method="GET",
            user=self.users["joel"],
            isJson=False,
            additionalHeaders=[("Range", "bytes=2-7")],
        )
        respQuery = self.request(
            path="/file/{_id}/download".format(**fobj),
            method="GET",
            user=self.users["joel"],
            isJson=False,
            params={"offset": 2, "endByte": 8},
        )
        for resp in [respHeader, respQuery]:
            self.assertEqual(file_contents[2:8].decode(), self.getBody(resp))
            self.assertEqual(resp.headers["Accept-Ranges"], "bytes")
            length = len(file_contents)
            begin, end = min(length, 2), min(length, 8)
            self.assertEqual(resp.headers["Content-Length"], end - begin)
            if length:
                self.assertStatus(resp, 206)
                self.assertEqual(
                    resp.headers["Content-Range"],
                    "bytes %d-%d/%d" % (begin, end - 1, length),
                )
            else:
                self.assertStatusOk(resp)

        resp = self.request(
            path="/file/{_id}".format(**fobj),
            method="DELETE",
            user=self.users["sally"],
            isJson=False,
        )
        self.assertStatusOk(resp)
        self.assertFalse(file1.exists())

    def _testUploadFile(self, name):
        """
        Uploads a non-empty file to the server.
        """
        # Initialize the upload
        resp = self.request(
            path="/file",
            method="POST",
            user=self.users["sally"],
            params={
                "parentType": "folder",
                "parentId": self.private_folder["_id"],
                "name": name,
                "size": len(chunk1) + len(chunk2),
                "mimeType": "text/plain",
            },
        )
        self.assertStatusOk(resp)

        uploadId = resp.json["_id"]

        # Uploading with no user should fail
        fields = [("offset", 0), ("uploadId", uploadId)]
        files = [("chunk", "helloWorld.txt", chunk1)]
        resp = self.multipartRequest(path="/file/chunk", fields=fields, files=files)
        self.assertStatus(resp, 401)

        # Uploading with the wrong user should fail
        fields = [("offset", 0), ("uploadId", uploadId)]
        files = [("chunk", "helloWorld.txt", chunk1)]
        resp = self.multipartRequest(
            path="/file/chunk", user=self.users["joel"], fields=fields, files=files
        )
        self.assertStatus(resp, 403)

        # Sending the first chunk should fail because the default minimum chunk
        # size is larger than our chunk.
        Setting().unset(SettingKey.UPLOAD_MINIMUM_CHUNK_SIZE)
        fields = [("offset", 0), ("uploadId", uploadId)]
        files = [("chunk", "helloWorld.txt", chunk1)]
        resp = self.multipartRequest(
            path="/file/chunk", user=self.users["sally"], fields=fields, files=files
        )
        self.assertStatus(resp, 400)
        self.assertEqual(
            resp.json,
            {
                "type": "validation",
                "message": "Chunk is smaller than the minimum size.",
            },
        )

        # Send the first chunk (use multipart)
        Setting().set(SettingKey.UPLOAD_MINIMUM_CHUNK_SIZE, 0)
        resp = self.multipartRequest(
            path="/file/chunk", user=self.users["sally"], fields=fields, files=files
        )
        self.assertStatusOk(resp)

        # Attempting to send second chunk with incorrect offset should fail
        fields = [("offset", 0), ("uploadId", uploadId)]
        files = [("chunk", name, chunk2)]
        resp = self.multipartRequest(
            path="/file/chunk", user=self.users["sally"], fields=fields, files=files
        )

        self.assertStatus(resp, 400)

        # Ask for completion before sending second chunk should fail
        resp = self.request(
            path="/file/completion",
            method="POST",
            user=self.users["sally"],
            params={"uploadId": uploadId},
        )
        self.assertStatus(resp, 400)

        # Request offset from server (simulate a resume event)
        resp = self.request(
            path="/file/offset", user=self.users["sally"], params={"uploadId": uploadId}
        )
        self.assertStatusOk(resp)

        # Trying to send too many bytes should fail
        currentOffset = resp.json["offset"]
        fields = [("offset", resp.json["offset"]), ("uploadId", uploadId)]
        files = [("chunk", name, "extra_" + chunk2 + "_bytes")]
        resp = self.multipartRequest(
            path="/file/chunk", user=self.users["sally"], fields=fields, files=files
        )
        self.assertStatus(resp, 400)
        self.assertEqual(
            resp.json, {"type": "validation", "message": "Received too many bytes."}
        )

        # The offset should not have changed
        resp = self.request(
            path="/file/offset", user=self.users["sally"], params={"uploadId": uploadId}
        )
        self.assertStatusOk(resp)
        self.assertEqual(resp.json["offset"], currentOffset)

        # Now upload the second chunk (using query params + body)
        resp = self.request(
            path="/file/chunk",
            method="POST",
            user=self.users["sally"],
            body=chunk2,
            params={"offset": resp.json["offset"], "uploadId": uploadId},
            type="text/plain",
        )
        self.assertStatusOk(resp)

        file = resp.json

        self.assertHasKeys(file, ["itemId"])
        self.assertEqual(file["name"], name)
        self.assertEqual(file["size"], len(chunk1 + chunk2))

        return file

    def test_upload_odd_cases(self):
        from girder.plugins.virtual_resources.rest import VirtualObject as vo

        # Change dest perms to ro
        dest_dir = pathlib.Path(self.private_folder["fsPath"])
        dest_dir.chmod(0o551)
        resp = self.request(
            path="/file",
            method="POST",
            user=self.users["sally"],
            params={
                "parentType": "folder",
                "parentId": self.private_folder["_id"],
                "name": "empty_file.txt",
                "size": "0",
                "mimeType": "text/plain",
            },
            exception=True,
        )
        self.assertStatus(resp, 500)
        self.assertEqual(
            resp.json["message"],
            "Insufficient perms to write on {}".format(self.private_folder["fsPath"]),
        )

        dest_dir.chmod(0o751)
        (dest_dir / "empty_file.txt").mkdir()
        resp = self.request(
            path="/file",
            method="POST",
            user=self.users["sally"],
            params={
                "parentType": "folder",
                "parentId": self.private_folder["_id"],
                "name": "empty_file.txt",
                "size": "0",
                "mimeType": "text/plain",
            },
            exception=True,
        )
        self.assertStatus(resp, 500)
        self.assertEqual(
            resp.json["message"],
            "IsADirectoryError: IsADirectoryError(21, 'Is a directory')",
        )
        (dest_dir / "empty_file.txt").rmdir()

        resp = self.request(
            path="/file",
            method="POST",
            user=self.users["sally"],
            params={
                "parentType": "folder",
                "parentId": self.private_folder["_id"],
                "name": "empty_file.txt",
                "size": 0,
                "mimeType": "text/plain",
            },
        )
        self.assertStatusOk(resp)

        resp = self.request(
            path="/file",
            method="POST",
            user=self.users["sally"],
            params={
                "parentType": "folder",
                "parentId": self.private_folder["_id"],
                "name": "blah_file.txt",
                "size": len(chunk1),
                "mimeType": "text/plain",
            },
        )
        self.assertStatusOk(resp)
        upload = resp.json

        (dest_dir / "blah_file.txt").unlink()
        dest_dir.chmod(0o551)
        resp = self.request(
            path="/file/chunk",
            method="POST",
            user=self.users["sally"],
            params={"uploadId": upload["_id"], "offset": 0},
            type="text/plain",
            body=chunk1,
            exception=True,
        )
        self.assertStatus(resp, 500)
        self.assertEqual(
            resp.json["message"], "Exception: Exception('Failed to store upload.',)",
        )
        dest_dir.chmod(0o775)

        resp = self.request(
            path="/file",
            method="POST",
            user=self.users["sally"],
            params={
                "parentType": "folder",
                "parentId": self.private_folder["_id"],
                "name": "full_body.txt",
                "size": len(chunk1),
                "mimeType": "text/plain",
            },
            type="text/plain",
            body=chunk1,
        )
        self.assertStatusOk(resp)
        fobj = resp.json
        self.assertEqual(fobj["size"], len(chunk1))
        self.assertEqual(fobj["_modelType"], "file")

        dest_dir = pathlib.Path(self.private_folder["fsPath"]) / "some_dir"
        dest_dir.mkdir()
        resp = self.request(
            path="/file",
            method="POST",
            user=self.users["sally"],
            params={
                "parentType": "folder",
                "parentId": vo.generate_id(
                    dest_dir.as_posix(), self.private_folder["_id"]
                ),
                "name": "full_body.txt",
                "size": len(chunk1),
                "mimeType": "text/plain",
            },
            type="text/plain",
            body=chunk1,
        )
        self.assertStatusOk(resp)
        fobj = resp.json
        self.assertEqual(fobj["size"], len(chunk1))
        self.assertTrue((dest_dir / fobj["name"]).is_file())

    def testFilesystemAssetstore(self):
        """
        Test usage of the Filesystem assetstore type.
        """
        self.assetstore = Assetstore().getCurrent()
        root = self.assetstore["root"]

        # Clean out the test assetstore on disk
        shutil.rmtree(root)

        # First clean out the temp directory
        tmpdir = os.path.join(root, "temp")
        if os.path.isdir(tmpdir):
            for tempname in os.listdir(tmpdir):
                os.remove(os.path.join(tmpdir, tempname))

        # Upload the two-chunk file
        file = self._testUploadFile("helloWorld1.txt")
        self.assertEqual(file["_modelType"], "file")

        # We want to make sure the file got uploaded correctly into
        # the assetstore and stored at the right location
        # self.assertEqual(os.stat(abspath).st_size, file["size"])
        # self.assertEqual(os.stat(abspath).st_mode & 0o777, DEFAULT_PERMS)

        # Make sure access control is enforced on download
        resp = self.request(path="/file/%s/download" % file["_id"], method="GET")
        self.assertStatus(resp, 401)

        # Make sure access control is enforced on get info
        resp = self.request(path="/file/" + str(file["_id"]), method="GET")
        self.assertStatus(resp, 401)

        # Make sure we can get the file info and that it's filtered
        resp = self.request(
            path="/file/" + str(file["_id"]), method="GET", user=self.users["sally"]
        )
        self.assertStatusOk(resp)
        self.assertEqual(resp.json["mimeType"], "application/octet-stream")  # FIXME
        self.assertEqual(resp.json["exts"], ["txt"])
        self.assertEqual(resp.json["_modelType"], "file")
        self.assertEqual(resp.json["creatorId"], str(self.users["sally"]["_id"]))
        self.assertEqual(resp.json["size"], file["size"])
        self.assertTrue("itemId" in resp.json)
        # self.assertTrue("assetstoreId" in resp.json)  # FIXME!
        # self.assertFalse("sha512" in resp.json)  # FIXME ?

    def tearDown(self):
        # Folder().remove(self.public_folder)
        # Folder().remove(self.private_folder)
        # Collection().remove(self.base_collection)
        for user in self.users.values():
            User().remove(user)
        for root in (self.public_root, self.shared_root, self.private_root):
            shutil.rmtree(root)
        super(FileOperationsTestCase, self).tearDown()
