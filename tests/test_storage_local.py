from pathlib import Path

import pytest

from app.storage.local import LocalStorageBackend


def test_local_storage_without_node_id_uses_root(tmp_path: Path):
    storage = LocalStorageBackend(str(tmp_path))

    assert storage.root == tmp_path.resolve()


def test_local_storage_with_node_id_uses_node_specific_root(
    tmp_path: Path,
):
    storage = LocalStorageBackend(
        str(tmp_path),
        node_id="node-a",
    )

    assert storage.root == (
        tmp_path / "nodes" / "node-a"
    ).resolve()


def test_different_nodes_use_different_roots(tmp_path: Path):
    node_a = LocalStorageBackend(
        str(tmp_path),
        node_id="node-a",
    )
    node_b = LocalStorageBackend(
        str(tmp_path),
        node_id="node-b",
    )

    assert node_a.root != node_b.root


def test_same_key_isolated_between_nodes(tmp_path: Path):
    node_a = LocalStorageBackend(
        str(tmp_path),
        node_id="node-a",
    )
    node_b = LocalStorageBackend(
        str(tmp_path),
        node_id="node-b",
    )

    key = "users/1/files/10/chunks/0/replica-0"

    node_a.put(key, b"data from node a")
    node_b.put(key, b"data from node b")

    assert node_a.get(key) == b"data from node a"
    assert node_b.get(key) == b"data from node b"

    assert (
        node_a.root / key
    ).exists()
    assert (
        node_b.root / key
    ).exists()

    assert (
        node_a.root / key
    ) != (
        node_b.root / key
    )


def test_delete_only_affects_selected_node(tmp_path: Path):
    node_a = LocalStorageBackend(
        str(tmp_path),
        node_id="node-a",
    )
    node_b = LocalStorageBackend(
        str(tmp_path),
        node_id="node-b",
    )

    key = "shared/object.bin"

    node_a.put(key, b"node-a-data")
    node_b.put(key, b"node-b-data")

    node_a.delete(key)

    with pytest.raises(FileNotFoundError):
        node_a.get(key)

    assert node_b.get(key) == b"node-b-data"


def test_storage_key_traversal_is_rejected(tmp_path: Path):
    storage = LocalStorageBackend(
        str(tmp_path),
        node_id="node-a",
    )

    with pytest.raises(ValueError, match="Unsafe storage key"):
        storage.put("../outside.txt", b"unsafe")