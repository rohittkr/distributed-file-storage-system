from pathlib import Path

import pytest

from app.storage.local import LocalStorageBackend


def test_local_storage_creates_root_directory(tmp_path: Path):
    root = tmp_path / "storage"

    assert not root.exists()

    storage = LocalStorageBackend(str(root))

    assert root.exists()
    assert root.is_dir()
    assert storage.root == root.resolve()


def test_local_storage_put_and_get(tmp_path: Path):
    storage = LocalStorageBackend(str(tmp_path / "storage"))

    storage.put("files/1/chunks/0", b"hello distributed storage")

    assert storage.get("files/1/chunks/0") == b"hello distributed storage"


def test_local_storage_put_creates_nested_directories(tmp_path: Path):
    root = tmp_path / "storage"
    storage = LocalStorageBackend(str(root))

    storage.put("users/106/files/21/chunks/0", b"chunk-data")

    stored_path = root / "users" / "106" / "files" / "21" / "chunks" / "0"

    assert stored_path.exists()
    assert stored_path.read_bytes() == b"chunk-data"


def test_local_storage_delete_removes_file(tmp_path: Path):
    storage = LocalStorageBackend(str(tmp_path / "storage"))

    storage.put("files/21/chunks/0", b"data")

    assert storage.get("files/21/chunks/0") == b"data"

    storage.delete("files/21/chunks/0")

    assert not (storage.root / "files" / "21" / "chunks" / "0").exists()


def test_local_storage_delete_missing_file_is_safe(tmp_path: Path):
    storage = LocalStorageBackend(str(tmp_path / "storage"))

    storage.delete("files/does-not-exist/chunks/0")


@pytest.mark.parametrize(
    "unsafe_key",
    [
        "../outside.txt",
        "../../outside.txt",
        "files/../../outside.txt",
        "/tmp/outside.txt",
    ],
)
def test_local_storage_rejects_path_traversal(tmp_path: Path, unsafe_key: str):
    storage = LocalStorageBackend(str(tmp_path / "storage"))

    with pytest.raises(ValueError, match="Unsafe storage key"):
        storage.put(unsafe_key, b"malicious data")


@pytest.mark.parametrize(
    "unsafe_key",
    [
        "../outside.txt",
        "../../outside.txt",
        "files/../../outside.txt",
        "/tmp/outside.txt",
    ],
)
def test_local_storage_rejects_unsafe_get_keys(
    tmp_path: Path,
    unsafe_key: str,
):
    storage = LocalStorageBackend(str(tmp_path / "storage"))

    with pytest.raises(ValueError, match="Unsafe storage key"):
        storage.get(unsafe_key)


@pytest.mark.parametrize(
    "unsafe_key",
    [
        "../outside.txt",
        "../../outside.txt",
        "files/../../outside.txt",
        "/tmp/outside.txt",
    ],
)
def test_local_storage_rejects_unsafe_delete_keys(
    tmp_path: Path,
    unsafe_key: str,
):
    storage = LocalStorageBackend(str(tmp_path / "storage"))

    with pytest.raises(ValueError, match="Unsafe storage key"):
        storage.delete(unsafe_key)