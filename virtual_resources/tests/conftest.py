import pathlib
import shutil
import tempfile

from girder.constants import AccessType

import pytest


@pytest.fixture
def extra_user(db, user):
    """
    Require an extra user.

    Provides an extra regular user on top of the user already provided by pytest_girder
    """
    from girder.models.user import User

    u = User().createUser(
        email="user2@girder.test",
        login="extra-user",
        firstName="user",
        lastName="extra",
        password="password",
        admin=False,
    )

    yield u


@pytest.fixture
def public_collection(db, user):
    from girder.models.collection import Collection

    public_collection = Collection().createCollection(
        "test_collection",
        creator=user,
        public=True,
        reuseExisting=True,
    )
    yield public_collection
    Collection().remove(public_collection)


@pytest.fixture
def private_folder(db, user, public_collection):
    from girder.models.folder import Folder

    private_folder = Folder().createFolder(
        public_collection,
        "private",
        creator=user,
        parentType="collection",
        public=False,
        reuseExisting=True,
    )
    Folder().setUserAccess(private_folder, user, AccessType.WRITE)
    yield private_folder
    Folder().remove(private_folder)


@pytest.fixture
def extra_public_folder(db, user, public_collection):
    from girder.models.folder import Folder

    public_folder = Folder().createFolder(
        public_collection,
        "extra_public",
        creator=user,
        parentType="collection",
        public=True,
        reuseExisting=True,
    )
    yield public_folder
    Folder().remove(public_folder)


@pytest.fixture
def public_folder(db, user, public_collection):
    from girder.models.folder import Folder

    public_folder = Folder().createFolder(
        public_collection,
        "public",
        creator=user,
        parentType="collection",
        public=True,
        reuseExisting=True,
    )
    yield public_folder
    Folder().remove(public_folder)


@pytest.fixture
def mapped_folder(db, public_folder):
    from girder.models.folder import Folder

    public_root = tempfile.mkdtemp()
    public_folder.update({"fsPath": public_root, "isMapping": True})
    mapped_folder = Folder().save(public_folder)
    yield mapped_folder
    mapped_folder.pop("fsPath")
    mapped_folder.pop("isMapping")
    shutil.rmtree(public_root, ignore_errors=True)
    mapped_folder = Folder().save(public_folder)


@pytest.fixture
def mapped_priv_folder(db, private_folder):
    from girder.models.folder import Folder

    private_root = tempfile.mkdtemp()
    private_folder.update({"fsPath": private_root, "isMapping": True})
    mapped_folder = Folder().save(private_folder)
    yield mapped_folder
    mapped_folder.pop("fsPath")
    mapped_folder.pop("isMapping")
    shutil.rmtree(private_root, ignore_errors=True)
    mapped_folder = Folder().save(private_folder)


@pytest.fixture
def example_mapped_folder(mapped_folder):
    root_path = pathlib.Path(mapped_folder["fsPath"])
    nested_dir = root_path / "level0"
    nested_dir.mkdir(parents=True)

    file1 = root_path / "some_file.txt"
    file1_contents = b"Blah Blah Blah"
    with file1.open(mode="wb") as fp:
        fp.write(file1_contents)

    file2 = nested_dir / "some_other_file.txt"
    file2_contents = b"Lorem ipsum..."
    with file2.open(mode="wb") as fp:
        fp.write(file2_contents)

    yield {
        "girder_root": mapped_folder,
        "nested_dir": nested_dir,
        "file1": file1,
        "file1_contents": file1_contents,
        "file2": file2,
        "file2_contents": file2_contents,
    }

    shutil.rmtree(root_path, ignore_errors=True)
