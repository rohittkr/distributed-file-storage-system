from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.main import app
from app.models.file import Chunk, ChunkReplica, FileVersion

client = TestClient(app)


def create_user_and_login() -> tuple[str, str]:
    email = f"{uuid4().hex}@example.com"
    password = "StrongPassword123!"

    register_response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
        },
    )
    assert register_response.status_code == 201

    login_response = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": password,
        },
    )
    assert login_response.status_code == 200

    token = login_response.json()["access_token"]
    return email, token


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_create_file_requires_authentication():
    response = client.post(
        "/api/v1/files",
        json={"name": "document.txt"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


def test_create_file_creates_metadata_for_authenticated_user():
    _, token = create_user_and_login()

    response = client.post(
        "/api/v1/files",
        headers=auth_headers(token),
        json={
            "name": "document.txt",
            "mime_type": "text/plain",
        },
    )

    assert response.status_code == 201

    body = response.json()

    assert body["name"] == "document.txt"
    assert body["mime_type"] == "text/plain"
    assert body["size_bytes"] == 0
    assert body["current_version_id"] is None
    assert "id" in body
    assert "created_at" in body
    assert "updated_at" in body
    assert "owner_id" not in body
    assert "password_hash" not in body


def test_list_files_returns_only_current_users_files():
    _, token_one = create_user_and_login()
    _, token_two = create_user_and_login()

    first_file = client.post(
        "/api/v1/files",
        headers=auth_headers(token_one),
        json={"name": "user-one.txt"},
    )
    assert first_file.status_code == 201

    second_file = client.post(
        "/api/v1/files",
        headers=auth_headers(token_two),
        json={"name": "user-two.txt"},
    )
    assert second_file.status_code == 201

    response = client.get(
        "/api/v1/files",
        headers=auth_headers(token_one),
    )

    assert response.status_code == 200

    files = response.json()

    assert len(files) == 1
    assert files[0]["name"] == "user-one.txt"


def test_get_file_returns_owner_file():
    _, token = create_user_and_login()

    create_response = client.post(
        "/api/v1/files",
        headers=auth_headers(token),
        json={"name": "report.pdf", "mime_type": "application/pdf"},
    )
    assert create_response.status_code == 201

    file_id = create_response.json()["id"]

    response = client.get(
        f"/api/v1/files/{file_id}",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    assert response.json()["id"] == file_id
    assert response.json()["name"] == "report.pdf"


def test_get_file_returns_404_for_another_users_file():
    _, owner_token = create_user_and_login()
    _, other_token = create_user_and_login()

    create_response = client.post(
        "/api/v1/files",
        headers=auth_headers(owner_token),
        json={"name": "private.txt"},
    )
    assert create_response.status_code == 201

    file_id = create_response.json()["id"]

    response = client.get(
        f"/api/v1/files/{file_id}",
        headers=auth_headers(other_token),
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "File not found."}


def test_get_nonexistent_file_returns_404():
    _, token = create_user_and_login()

    response = client.get(
        "/api/v1/files/999999999",
        headers=auth_headers(token),
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "File not found."}


def test_update_file_updates_owner_file():
    _, token = create_user_and_login()

    create_response = client.post(
        "/api/v1/files",
        headers=auth_headers(token),
        json={"name": "old-name.txt"},
    )
    assert create_response.status_code == 201

    file_id = create_response.json()["id"]

    response = client.patch(
        f"/api/v1/files/{file_id}",
        headers=auth_headers(token),
        json={
            "name": "new-name.txt",
            "mime_type": "text/plain",
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert body["id"] == file_id
    assert body["name"] == "new-name.txt"
    assert body["mime_type"] == "text/plain"


def test_update_file_cannot_modify_another_users_file():
    _, owner_token = create_user_and_login()
    _, other_token = create_user_and_login()

    create_response = client.post(
        "/api/v1/files",
        headers=auth_headers(owner_token),
        json={"name": "owner-file.txt"},
    )
    assert create_response.status_code == 201

    file_id = create_response.json()["id"]

    response = client.patch(
        f"/api/v1/files/{file_id}",
        headers=auth_headers(other_token),
        json={"name": "hacked.txt"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "File not found."}

    owner_response = client.get(
        f"/api/v1/files/{file_id}",
        headers=auth_headers(owner_token),
    )

    assert owner_response.status_code == 200
    assert owner_response.json()["name"] == "owner-file.txt"


def test_delete_file_deletes_owner_file():
    _, token = create_user_and_login()

    create_response = client.post(
        "/api/v1/files",
        headers=auth_headers(token),
        json={"name": "delete-me.txt"},
    )
    assert create_response.status_code == 201

    file_id = create_response.json()["id"]

    response = client.delete(
        f"/api/v1/files/{file_id}",
        headers=auth_headers(token),
    )

    assert response.status_code == 204
    assert response.content == b""

    get_response = client.get(
        f"/api/v1/files/{file_id}",
        headers=auth_headers(token),
    )

    assert get_response.status_code == 404


def test_delete_file_cannot_delete_another_users_file():
    _, owner_token = create_user_and_login()
    _, other_token = create_user_and_login()

    create_response = client.post(
        "/api/v1/files",
        headers=auth_headers(owner_token),
        json={"name": "protected.txt"},
    )
    assert create_response.status_code == 201

    file_id = create_response.json()["id"]

    response = client.delete(
        f"/api/v1/files/{file_id}",
        headers=auth_headers(other_token),
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "File not found."}

    owner_response = client.get(
        f"/api/v1/files/{file_id}",
        headers=auth_headers(owner_token),
    )

    assert owner_response.status_code == 200
    assert owner_response.json()["name"] == "protected.txt"


def test_create_file_rejects_empty_name():
    _, token = create_user_and_login()

    response = client.post(
        "/api/v1/files",
        headers=auth_headers(token),
        json={"name": ""},
    )

    assert response.status_code == 422


def test_create_file_rejects_name_longer_than_512_characters():
    _, token = create_user_and_login()

    response = client.post(
        "/api/v1/files",
        headers=auth_headers(token),
        json={"name": "x" * 513},
    )

    assert response.status_code == 422


def test_file_response_does_not_expose_sensitive_fields():
    _, token = create_user_and_login()

    response = client.post(
        "/api/v1/files",
        headers=auth_headers(token),
        json={"name": "safe.txt"},
    )

    assert response.status_code == 201

    body = response.json()

    assert "password" not in body
    assert "password_hash" not in body
    assert "owner_id" not in body


def test_upload_file_content_creates_version_and_stores_content():
    _, token = create_user_and_login()

    create_response = client.post(
        "/api/v1/files",
        headers=auth_headers(token),
        json={
            "name": "upload-test.txt",
            "mime_type": "text/plain",
        },
    )

    assert create_response.status_code == 201

    file_id = create_response.json()["id"]
    content = b"Hello from Phase 4 storage!"

    response = client.post(
        f"/api/v1/files/{file_id}/content",
        headers=auth_headers(token),
        files={
            "upload": (
                "upload-test.txt",
                content,
                "text/plain",
            )
        },
    )

    assert response.status_code == 201

    body = response.json()

    assert body["id"] == file_id
    assert body["name"] == "upload-test.txt"
    assert body["mime_type"] == "text/plain"
    assert body["size_bytes"] == len(content)
    assert body["current_version_id"] is not None


def test_download_file_content_returns_uploaded_content():
    _, token = create_user_and_login()

    create_response = client.post(
        "/api/v1/files",
        headers=auth_headers(token),
        json={
            "name": "download-test.txt",
            "mime_type": "text/plain",
        },
    )

    assert create_response.status_code == 201

    file_id = create_response.json()["id"]
    content = b"Download this exact content."

    upload_response = client.post(
        f"/api/v1/files/{file_id}/content",
        headers=auth_headers(token),
        files={
            "upload": (
                "download-test.txt",
                content,
                "text/plain",
            )
        },
    )

    assert upload_response.status_code == 201

    download_response = client.get(
        f"/api/v1/files/{file_id}/content",
        headers=auth_headers(token),
    )

    assert download_response.status_code == 200
    assert download_response.content == content
    assert download_response.headers["content-type"] == "text/plain; charset=utf-8"
    assert "attachment" in download_response.headers["content-disposition"]
    assert "download-test.txt" in download_response.headers["content-disposition"]


def test_upload_file_content_requires_authentication():
    response = client.post(
        "/api/v1/files/1/content",
        files={
            "upload": (
                "unauthorized-upload.txt",
                b"unauthorized",
                "text/plain",
            )
        },
    )

    assert response.status_code == 401


def test_download_file_content_requires_authentication():
    response = client.get("/api/v1/files/1/content")

    assert response.status_code == 401


def test_upload_file_content_rejects_another_users_file():
    _, owner_token = create_user_and_login()
    _, other_token = create_user_and_login()

    create_response = client.post(
        "/api/v1/files",
        headers=auth_headers(owner_token),
        json={
            "name": "private-upload.txt",
            "mime_type": "text/plain",
        },
    )

    assert create_response.status_code == 201

    file_id = create_response.json()["id"]

    response = client.post(
        f"/api/v1/files/{file_id}/content",
        headers=auth_headers(other_token),
        files={
            "upload": (
                "private-upload.txt",
                b"secret",
                "text/plain",
            )
        },
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "File not found."}


def test_download_file_content_rejects_another_users_file():
    _, owner_token = create_user_and_login()
    _, other_token = create_user_and_login()

    create_response = client.post(
        "/api/v1/files",
        headers=auth_headers(owner_token),
        json={
            "name": "private-download.txt",
            "mime_type": "text/plain",
        },
    )

    assert create_response.status_code == 201

    file_id = create_response.json()["id"]

    upload_response = client.post(
        f"/api/v1/files/{file_id}/content",
        headers=auth_headers(owner_token),
        files={
            "upload": (
                "private-download.txt",
                b"private content",
                "text/plain",
            )
        },
    )

    assert upload_response.status_code == 201

    response = client.get(
        f"/api/v1/files/{file_id}/content",
        headers=auth_headers(other_token),
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "File not found."}


def test_upload_file_content_splits_large_file_into_chunks():
    _, token = create_user_and_login()

    create_response = client.post(
        "/api/v1/files",
        headers=auth_headers(token),
        json={
            "name": "large-file.bin",
            "mime_type": "application/octet-stream",
        },
    )

    assert create_response.status_code == 201

    file_id = create_response.json()["id"]
    chunk_size = settings.chunk_size_bytes

    content = (
        b"A" * chunk_size
        + b"B" * chunk_size
        + b"C" * 123
    )

    upload_response = client.post(
        f"/api/v1/files/{file_id}/content",
        headers=auth_headers(token),
        files={
            "upload": (
                "large-file.bin",
                content,
                "application/octet-stream",
            )
        },
    )

    assert upload_response.status_code == 201

    body = upload_response.json()

    assert body["id"] == file_id
    assert body["size_bytes"] == len(content)
    assert body["current_version_id"] is not None

    with SessionLocal() as db:
        version = db.scalar(
            select(FileVersion).where(
                FileVersion.id == body["current_version_id"]
            )
        )

        assert version is not None
        assert version.size_bytes == len(content)

        chunks = list(
            db.scalars(
                select(Chunk)
                .where(Chunk.version_id == version.id)
                .order_by(Chunk.chunk_number)
            ).all()
        )

        replicas = list(
            db.scalars(
                select(ChunkReplica)
                .join(Chunk, Chunk.id == ChunkReplica.chunk_id)
                .where(Chunk.version_id == version.id)
                .order_by(Chunk.chunk_number)
            ).all()
        )

    assert len(chunks) == 3
    assert len(replicas) == 3

    assert chunks[0].chunk_number == 0
    assert chunks[0].size_bytes == chunk_size

    assert chunks[1].chunk_number == 1
    assert chunks[1].size_bytes == chunk_size

    assert chunks[2].chunk_number == 2
    assert chunks[2].size_bytes == 123

    assert all(chunk.checksum for chunk in chunks)
    assert all(chunk.content_hash for chunk in chunks)

    assert replicas[0].storage_key.endswith("/chunks/0")
    assert replicas[1].storage_key.endswith("/chunks/1")
    assert replicas[2].storage_key.endswith("/chunks/2")


def test_download_file_content_reconstructs_large_file_from_chunks():
    _, token = create_user_and_login()

    create_response = client.post(
        "/api/v1/files",
        headers=auth_headers(token),
        json={
            "name": "reconstruct-large.bin",
            "mime_type": "application/octet-stream",
        },
    )

    assert create_response.status_code == 201

    file_id = create_response.json()["id"]
    chunk_size = settings.chunk_size_bytes

    content = (
        b"A" * chunk_size
        + b"B" * chunk_size
        + b"C" * 123
    )

    upload_response = client.post(
        f"/api/v1/files/{file_id}/content",
        headers=auth_headers(token),
        files={
            "upload": (
                "reconstruct-large.bin",
                content,
                "application/octet-stream",
            )
        },
    )

    assert upload_response.status_code == 201

    download_response = client.get(
        f"/api/v1/files/{file_id}/content",
        headers=auth_headers(token),
    )

    assert download_response.status_code == 200
    assert download_response.content == content