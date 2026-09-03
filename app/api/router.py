from datetime import datetime, timedelta, timezone
from hashlib import sha256
from math import ceil
import secrets


from fastapi import (
    APIRouter,
    Depends,
    File as FastAPIFile,
    Header,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.cache import (
    cache_file,
    get_cached_file,
    invalidate_file_cache,
)
from app.core.config import settings
from app.core.redis import redis_client
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models.file import (
    Chunk,
    ChunkReplica,
    File,
    FileVersion,
    StorageNode,
    UploadSession,
)
from app.services.replica_repair import get_healthy_chunk_replicas
from app.models.share import Share

from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.schemas.files import (
    CompleteUploadResponse,
    FileCreateRequest,
    FileResponse,
    FileUpdateRequest,
    FileVersionListResponse,
    FileVersionResponse,
    ShareCreateRequest,
    ShareCreateResponse,
    ShareResponse,
    StorageNodeCreateRequest,
    StorageNodeHeartbeatResponse,
    StorageNodeResponse,
    UploadChunkResponse,
    UploadSessionCreateRequest,
    UploadSessionResponse,
)

from app.storage.local import LocalStorageBackend


api_router = APIRouter()


def get_healthy_storage_nodes(
    db: Session,
    limit: int | None = None,
    exclude_node_ids: set[int] | None = None,
) -> list[StorageNode]:
    statement = (
        select(StorageNode)
        .where(StorageNode.status == "healthy")
        .order_by(StorageNode.used_bytes.asc(), StorageNode.id.asc())
    )

    if exclude_node_ids:
        statement = statement.where(
            ~StorageNode.id.in_(exclude_node_ids)
        )

    if limit is not None:
        statement = statement.limit(limit)

    return list(db.scalars(statement).all())


def get_healthy_storage_node(db: Session) -> StorageNode:
    storage_nodes = get_healthy_storage_nodes(db, limit=1)

    if storage_nodes:
        return storage_nodes[0]

    storage_node = StorageNode(
        node_id="local",
        endpoint="local://storage",
        status="healthy",
        capacity_bytes=0,
        used_bytes=0,
    )

    db.add(storage_node)
    db.flush()

    return storage_node


def serialize_upload_session(
    upload_session: UploadSession,
) -> UploadSessionResponse:
    if upload_session.file_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Upload session is not associated with a file.",
        )

    return UploadSessionResponse(
        id=upload_session.id,
        file_id=upload_session.file_id,
        filename=upload_session.filename,
        mime_type=upload_session.mime_type,
        total_size_bytes=upload_session.total_size_bytes,
        chunk_size_bytes=upload_session.chunk_size_bytes,
        total_chunks=upload_session.total_chunks,
        received_chunks=upload_session.received_chunks,
        status=upload_session.status,
        created_at=upload_session.created_at,
        expires_at=upload_session.expires_at,
        completed_at=upload_session.completed_at,
    )


def get_upload_session_for_user(
    session_id: int,
    current_user: User,
    db: Session,
) -> UploadSession:
    upload_session = db.scalar(
        select(UploadSession).where(
            UploadSession.id == session_id,
            UploadSession.owner_id == current_user.id,
        )
    )

    if upload_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Upload session not found.",
        )

    return upload_session


def ensure_upload_session_active(
    upload_session: UploadSession,
    db: Session,
) -> None:
    if upload_session.status == "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Upload session is already completed.",
        )

    if upload_session.status != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Upload session is not active.",
        )

    now = datetime.now(timezone.utc)

    if upload_session.expires_at <= now:
        upload_session.status = "expired"
        db.commit()

        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Upload session has expired.",
        )


def validate_chunk_number(
    chunk_number: int,
    total_chunks: int,
) -> None:
    if chunk_number < 0 or chunk_number >= total_chunks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Chunk number must be between 0 and "
                f"{total_chunks - 1}."
            ),
        )


@api_router.get("/version")
def version() -> dict[str, str]:
    return {"version": "0.1.0", "status": "foundation"}

@api_router.post(
    "/storage/nodes",
    response_model=StorageNodeResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["storage"],
)
def create_storage_node(
    payload: StorageNodeCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StorageNode:
    existing_node = db.scalar(
        select(StorageNode).where(
            StorageNode.node_id == payload.node_id
        )
    )

    if existing_node is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A storage node with this node_id already exists.",
        )

    storage_node = StorageNode(
        node_id=payload.node_id,
        endpoint=payload.endpoint,
        status="healthy",
        capacity_bytes=payload.capacity_bytes,
        used_bytes=0,
        last_heartbeat=datetime.now(timezone.utc),
    )

    db.add(storage_node)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A storage node with this node_id already exists.",
        ) from None

    db.refresh(storage_node)
    return storage_node


@api_router.get(
    "/storage/nodes",
    response_model=list[StorageNodeResponse],
    tags=["storage"],
)
def list_storage_nodes(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[StorageNode]:
    statement = select(StorageNode).order_by(StorageNode.id.asc())
    return list(db.scalars(statement).all())


@api_router.get(
    "/storage/nodes/{node_id}",
    response_model=StorageNodeResponse,
    tags=["storage"],
)
def get_storage_node(
    node_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StorageNode:
    storage_node = db.scalar(
        select(StorageNode).where(
            StorageNode.node_id == node_id
        )
    )

    if storage_node is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Storage node not found.",
        )

    return storage_node


@api_router.post(
    "/storage/nodes/{node_id}/heartbeat",
    response_model=StorageNodeHeartbeatResponse,
    tags=["storage"],
)
def storage_node_heartbeat(
    node_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StorageNode:
    storage_node = db.scalar(
        select(StorageNode).where(
            StorageNode.node_id == node_id
        )
    )

    if storage_node is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Storage node not found.",
        )

    storage_node.status = "healthy"
    storage_node.last_heartbeat = datetime.now(timezone.utc)

    db.commit()
    db.refresh(storage_node)

    return storage_node


@api_router.post(
    "/storage/nodes/{node_id}/fail",
    response_model=StorageNodeResponse,
    tags=["storage"],
)
def fail_storage_node(
    node_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StorageNode:
    storage_node = db.scalar(
        select(StorageNode).where(
            StorageNode.node_id == node_id
        )
    )

    if storage_node is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Storage node not found.",
        )

    if storage_node.status == "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Storage node is already failed.",
        )

    storage_node.status = "failed"

    db.commit()
    db.refresh(storage_node)

    return storage_node


@api_router.post(
    "/storage/nodes/{node_id}/recover",
    response_model=StorageNodeResponse,
    tags=["storage"],
)
def recover_storage_node(
    node_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StorageNode:
    storage_node = db.scalar(
        select(StorageNode).where(
            StorageNode.node_id == node_id
        )
    )

    if storage_node is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Storage node not found.",
        )

    if storage_node.status == "healthy":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Storage node is already healthy.",
        )

    storage_node.status = "healthy"
    storage_node.last_heartbeat = datetime.now(timezone.utc)

    db.commit()
    db.refresh(storage_node)

    return storage_node


@api_router.post(
    "/auth/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["auth"],
)
def register(
    payload: RegisterRequest,
    db: Session = Depends(get_db),
) -> User:
    email = payload.email.strip().lower()

    existing_user = db.scalar(
        select(User).where(User.email == email)
    )

    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
    )

    db.add(user)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        ) from None

    db.refresh(user)

    return user


@api_router.post(
    "/auth/login",
    response_model=TokenResponse,
    tags=["auth"],
)
def login(
    payload: LoginRequest,
    db: Session = Depends(get_db),
) -> TokenResponse:
    email = payload.email.strip().lower()

    user = db.scalar(
        select(User).where(User.email == email)
    )

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(
        payload.password,
        user.password_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return TokenResponse(
        access_token=create_access_token(str(user.id)),
    )


@api_router.get(
    "/auth/me",
    response_model=UserResponse,
    tags=["auth"],
)
def get_me(
    current_user: User = Depends(get_current_user),
) -> User:
    return current_user


@api_router.post(
    "/files",
    response_model=FileResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["files"],
)
def create_file(
    payload: FileCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> File:
    file = File(
        owner_id=current_user.id,
        name=payload.name.strip(),
        mime_type=payload.mime_type,
        size_bytes=0,
    )

    db.add(file)
    db.commit()
    db.refresh(file)

    return file


@api_router.get(
    "/files",
    response_model=list[FileResponse],
    tags=["files"],
)
def list_files(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[File]:
    statement = (
        select(File)
        .where(File.owner_id == current_user.id)
        .order_by(File.created_at.desc(), File.id.desc())
    )

    return list(db.scalars(statement).all())


@api_router.get(
    "/files/{file_id}",
    response_model=FileResponse,
    tags=["files"],
)
def get_file(
    file_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    cached_file = get_cached_file(
        file_id,
        current_user.id,
    )

    if cached_file is not None:
        return FileResponse.model_validate(cached_file)

    statement = select(File).where(
        File.id == file_id,
        File.owner_id == current_user.id,
    )

    file = db.scalar(statement)

    if file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found.",
        )

    response = FileResponse.model_validate(file)

    cache_file(
        file.id,
        current_user.id,
        response.model_dump(mode="json"),
    )

    return response


@api_router.patch(
    "/files/{file_id}",
    response_model=FileResponse,
    tags=["files"],
)
def update_file(
    file_id: int,
    payload: FileUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> File:
    statement = select(File).where(
        File.id == file_id,
        File.owner_id == current_user.id,
    )

    file = db.scalar(statement)

    if file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found.",
        )

    if payload.name is not None:
        file.name = payload.name.strip()

    if payload.mime_type is not None:
        file.mime_type = payload.mime_type

    db.commit()
    db.refresh(file)

    invalidate_file_cache(
        file.id,
        current_user.id,
    )

    return file

@api_router.delete(
    "/files/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["files"],
)
def delete_file(
    file_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    statement = select(File).where(
        File.id == file_id,
        File.owner_id == current_user.id,
    )

    file = db.scalar(statement)

    if file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found.",
        )

    db.delete(file)
    db.commit()

    invalidate_file_cache(
        file.id,
        current_user.id,
    )

@api_router.post(
    "/files/{file_id}/content",
    response_model=FileResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["files"],
)
def upload_file_content(
    file_id: int,
    upload: UploadFile = FastAPIFile(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> File:
    statement = select(File).where(
        File.id == file_id,
        File.owner_id == current_user.id,
    )

    file = db.scalar(statement)

    if file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found.",
        )

    data = upload.file.read()

    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"File exceeds the maximum upload size of "
                f"{settings.max_upload_bytes} bytes."
            ),
        )

    checksum = sha256(data).hexdigest()

    latest_version_number = db.scalar(
        select(FileVersion.version_number)
        .where(FileVersion.file_id == file.id)
        .order_by(FileVersion.version_number.desc())
        .limit(1)
    )

    version_number = (latest_version_number or 0) + 1

    version = FileVersion(
        file_id=file.id,
        version_number=version_number,
        size_bytes=len(data),
        checksum=checksum,
    )

    db.add(version)
    db.flush()

    chunk_size = settings.chunk_size_bytes
    replication_factor = settings.replication_factor

    if chunk_size <= 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Chunk size must be greater than zero.",
        )

    if replication_factor < 1:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Replication factor must be at least 1.",
        )

    for chunk_number, offset in enumerate(
        range(0, len(data), chunk_size)
    ):
        chunk_data = data[offset : offset + chunk_size]

        chunk_checksum = sha256(chunk_data).hexdigest()

        chunk = Chunk(
            version_id=version.id,
            chunk_number=chunk_number,
            size_bytes=len(chunk_data),
            checksum=chunk_checksum,
            content_hash=chunk_checksum,
        )

        db.add(chunk)
        db.flush()

        storage_nodes = get_healthy_storage_nodes(
            db,
            limit=replication_factor,
        )

        if len(storage_nodes) < replication_factor:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Not enough healthy storage nodes for the required "
                    f"replication factor of {replication_factor}."
                ),
            )

        for replica_number, storage_node in enumerate(
            storage_nodes
        ):
            storage_key = (
                f"users/{current_user.id}/files/{file.id}/"
                f"versions/{version_number}/"
                f"chunks/{chunk_number}/"
                f"replica-{replica_number}"
            )

            storage = LocalStorageBackend(
                settings.local_storage_root,
                node_id=storage_node.node_id,
            )

            storage.put(
                storage_key,
                chunk_data,
            )

            replica = ChunkReplica(
                chunk_id=chunk.id,
                storage_node_id=storage_node.id,
                storage_key=storage_key,
                status="healthy",
                checksum=chunk_checksum,
            )

            db.add(replica)

            storage_node.used_bytes += len(chunk_data)

    file.size_bytes = len(data)
    file.current_version_id = version.id

    db.commit()
    db.refresh(file)

    return file


@api_router.get(
    "/files/{file_id}/content",
    tags=["files"],
)
def download_file_content(
    file_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    statement = select(File).where(
        File.id == file_id,
        File.owner_id == current_user.id,
    )

    file = db.scalar(statement)

    if file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found.",
        )

    if file.current_version_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File content not found.",
        )

    chunks = list(
        db.scalars(
            select(Chunk)
            .where(
                Chunk.version_id == file.current_version_id
            )
            .order_by(Chunk.chunk_number)
        ).all()
    )

    if not chunks:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File content not found.",
        )

    content_parts: list[bytes] = []

    for chunk in chunks:
        replicas = get_healthy_chunk_replicas(
        db,
        chunk.id,
    )

        if not replicas:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File content not found.",
            )

        chunk_data: bytes | None = None

        for replica in replicas:
            storage_node = db.get(
                StorageNode,
                replica.storage_node_id,
            )

            if storage_node is None:
                continue

            storage = LocalStorageBackend(
                settings.local_storage_root,
                node_id=storage_node.node_id,
            )

            try:
                candidate_data = storage.get(
                    replica.storage_key
                )
            except FileNotFoundError:
                continue

            if sha256(candidate_data).hexdigest() != chunk.checksum:
                continue

            chunk_data = candidate_data
            break

        if chunk_data is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "Stored file content failed "
                    "integrity verification."
                ),
            )

        content_parts.append(chunk_data)

    data = b"".join(content_parts)

    return Response(
        content=data,
        media_type=file.mime_type or "application/octet-stream",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{file.name}"'
            ),
        },
    )


# ---------------------------------------------------------------------------
# Resumable uploads
# ---------------------------------------------------------------------------


@api_router.post(
    "/uploads",
    response_model=UploadSessionResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["uploads"],
)
def create_upload_session(
    payload: UploadSessionCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
) -> UploadSessionResponse:
    if payload.total_size_bytes > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"File exceeds the maximum upload size of "
                f"{settings.max_upload_bytes} bytes."
            ),
        )

    chunk_size = settings.chunk_size_bytes

    if chunk_size <= 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Chunk size must be greater than zero.",
        )

    total_chunks = (
        ceil(payload.total_size_bytes / chunk_size)
        if payload.total_size_bytes > 0
        else 0
    )

    idempotency_cache_key = None
    idempotency_lock_key = None
    lock_owner_token = None
    lock_acquired = False

    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()

        if not idempotency_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Idempotency-Key must not be empty.",
            )

        idempotency_cache_key = (
            f"upload:idempotency:{current_user.id}:{idempotency_key}"
        )
        idempotency_lock_key = (
            f"upload:idempotency:lock:{current_user.id}:{idempotency_key}"
        )

        existing_session_id = redis_client.get(
            idempotency_cache_key
        )

        if existing_session_id is not None:
            try:
                existing_session = db.scalar(
                    select(UploadSession).where(
                        UploadSession.id == int(existing_session_id),
                        UploadSession.owner_id == current_user.id,
                    )
                )
            except (TypeError, ValueError):
                existing_session = None

            if existing_session is not None:
                return serialize_upload_session(
                    existing_session
                )

        lock_owner_token = secrets.token_urlsafe(32)

        lock_acquired = redis_client.set_if_not_exists(
            idempotency_lock_key,
            lock_owner_token,
            ttl_seconds=30,
        )

        if not lock_acquired:
            existing_session_id = redis_client.get(
                idempotency_cache_key
            )

            if existing_session_id is not None:
                try:
                    existing_session = db.scalar(
                        select(UploadSession).where(
                            UploadSession.id == int(existing_session_id),
                            UploadSession.owner_id == current_user.id,
                        )
                    )
                except (TypeError, ValueError):
                    existing_session = None

                if existing_session is not None:
                    return serialize_upload_session(
                        existing_session
                    )

            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An upload with this Idempotency-Key is already being created.",
            )

    try:
        file = File(
            owner_id=current_user.id,
            name=payload.filename.strip(),
            mime_type=payload.mime_type,
            size_bytes=0,
            current_version_id=None,
        )

        db.add(file)
        db.flush()

        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

        upload_session = UploadSession(
            owner_id=current_user.id,
            file_id=file.id,
            filename=payload.filename.strip(),
            mime_type=payload.mime_type,
            total_size_bytes=payload.total_size_bytes,
            chunk_size_bytes=chunk_size,
            total_chunks=total_chunks,
            received_chunks=0,
            status="active",
            expires_at=expires_at,
        )

        db.add(upload_session)
        db.commit()
        db.refresh(upload_session)

        if idempotency_cache_key is not None:
            redis_client.set(
                idempotency_cache_key,
                upload_session.id,
                ttl_seconds=24 * 60 * 60,
            )

        return serialize_upload_session(upload_session)

    finally:
        if (
            lock_acquired
            and idempotency_lock_key is not None
            and lock_owner_token is not None
        ):
            redis_client.release_if_owner(
                idempotency_lock_key,
                lock_owner_token,
            )


@api_router.get(
    "/uploads/{session_id}",
    response_model=UploadSessionResponse,
    tags=["uploads"],
)
def get_upload_session(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UploadSessionResponse:
    upload_session = get_upload_session_for_user(
        session_id,
        current_user,
        db,
    )

    if (
        upload_session.status == "active"
        and upload_session.expires_at <= datetime.now(timezone.utc)
    ):
        upload_session.status = "expired"
        db.commit()
        db.refresh(upload_session)

    return serialize_upload_session(upload_session)


@api_router.post(
    "/uploads/{session_id}/chunks/{chunk_number}",
    response_model=UploadChunkResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["uploads"],
)
def upload_resumable_chunk(
    session_id: int,
    chunk_number: int,
    upload: UploadFile = FastAPIFile(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UploadChunkResponse:
    upload_session = get_upload_session_for_user(
        session_id,
        current_user,
        db,
    )

    ensure_upload_session_active(
        upload_session,
        db,
    )

    validate_chunk_number(
        chunk_number,
        upload_session.total_chunks,
    )

    if upload_session.file_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Upload session is not associated with a file.",
        )

    file = db.get(
        File,
        upload_session.file_id,
    )

    if file is None or file.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found.",
        )

    chunk_data = upload.file.read()

    expected_size = upload_session.chunk_size_bytes

    if chunk_number < upload_session.total_chunks - 1:
        if len(chunk_data) != expected_size:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Chunk {chunk_number} must be exactly "
                    f"{expected_size} bytes."
                ),
            )
    else:
        remaining_size = (
            upload_session.total_size_bytes
            - (
                expected_size
                * max(upload_session.total_chunks - 1, 0)
            )
        )

        if len(chunk_data) != remaining_size:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Final chunk must be exactly "
                    f"{remaining_size} bytes."
                ),
            )

    existing_chunk = db.scalar(
        select(Chunk)
        .join(FileVersion, Chunk.version_id == FileVersion.id)
        .where(
            FileVersion.file_id == file.id,
            FileVersion.version_number == -session_id,
            Chunk.chunk_number == chunk_number,
        )
    )

    if existing_chunk is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Chunk has already been uploaded.",
        )

    chunk_checksum = sha256(chunk_data).hexdigest()

    replication_factor = settings.replication_factor

    if replication_factor < 1:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Replication factor must be at least 1.",
        )

    storage_nodes = get_healthy_storage_nodes(
        db,
        limit=replication_factor,
    )

    if len(storage_nodes) < replication_factor:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Not enough healthy storage nodes for the required "
                f"replication factor of {replication_factor}."
            ),
        )

    # A temporary FileVersion is created for each resumable upload.
    # Its final checksum and size are replaced during completion.
    version = db.scalar(
        select(FileVersion).where(
            FileVersion.file_id == file.id,
            FileVersion.version_number == -session_id,
        )
    )

    if version is None:
        version = FileVersion(
            file_id=file.id,
            version_number=-session_id,
            size_bytes=0,
            checksum=sha256(b"").hexdigest(),
        )
        db.add(version)
        db.flush()

    chunk = Chunk(
        version_id=version.id,
        chunk_number=chunk_number,
        size_bytes=len(chunk_data),
        checksum=chunk_checksum,
        content_hash=chunk_checksum,
    )

    db.add(chunk)
    db.flush()

    for replica_number, storage_node in enumerate(storage_nodes):
        storage_key = (
            f"users/{current_user.id}/files/{file.id}/"
            f"uploads/{session_id}/"
            f"chunks/{chunk_number}/"
            f"replica-{replica_number}"
        )
        storage = LocalStorageBackend(
        settings.local_storage_root,
        node_id=storage_node.node_id,
    )

        storage.put(
            storage_key,
            chunk_data,
        )

        replica = ChunkReplica(
            chunk_id=chunk.id,
            storage_node_id=storage_node.id,
            storage_key=storage_key,
            status="healthy",
            checksum=chunk_checksum,
        )

        db.add(replica)

        storage_node.used_bytes += len(chunk_data)

    upload_session.received_chunks += 1

    db.commit()

    return UploadChunkResponse(
        session_id=upload_session.id,
        chunk_number=chunk_number,
        size_bytes=len(chunk_data),
        received_chunks=upload_session.received_chunks,
        total_chunks=upload_session.total_chunks,
        status=upload_session.status,
    )


@api_router.post(
    "/uploads/{session_id}/complete",
    response_model=CompleteUploadResponse,
    tags=["uploads"],
)
def complete_upload(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CompleteUploadResponse:
    upload_session = get_upload_session_for_user(
        session_id,
        current_user,
        db,
    )

    ensure_upload_session_active(
        upload_session,
        db,
    )

    if upload_session.file_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Upload session is not associated with a file.",
        )

    if upload_session.received_chunks != upload_session.total_chunks:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Upload is incomplete. Received "
                f"{upload_session.received_chunks} of "
                f"{upload_session.total_chunks} chunks."
            ),
        )

    file = db.get(
        File,
        upload_session.file_id,
    )

    if file is None or file.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found.",
        )

    temporary_version = db.scalar(
        select(FileVersion).where(
            FileVersion.file_id == file.id,
            FileVersion.version_number == -session_id,
        )
    )

    if temporary_version is None:
        if upload_session.total_chunks == 0:
            latest_version_number = db.scalar(
                select(FileVersion.version_number)
                .where(FileVersion.file_id == file.id)
                .order_by(FileVersion.version_number.desc())
                .limit(1)
            )

            version_number = (latest_version_number or 0) + 1

            version = FileVersion(
                file_id=file.id,
                version_number=version_number,
                size_bytes=0,
                checksum=sha256(b"").hexdigest(),
            )

            db.add(version)
            db.flush()

            file.size_bytes = 0
            file.current_version_id = version.id

            upload_session.status = "completed"
            upload_session.completed_at = datetime.now(timezone.utc)

            db.commit()
            db.refresh(file)
            db.refresh(upload_session)

            return CompleteUploadResponse(
                file=file,
                session=serialize_upload_session(upload_session),
            )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Upload chunks were not found.",
        )

    chunks = list(
        db.scalars(
            select(Chunk)
            .where(Chunk.version_id == temporary_version.id)
            .order_by(Chunk.chunk_number)
        ).all()
    )

    if len(chunks) != upload_session.total_chunks:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Upload chunks are incomplete.",
        )

    content_parts: list[bytes] = []

    for expected_number, chunk in enumerate(chunks):
        if chunk.chunk_number != expected_number:
            raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Upload chunks are incomplete or out of order.",
        )

        replicas = get_healthy_chunk_replicas(
        db,
        chunk.id,
    )

        if not replicas:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"No healthy replica exists for chunk "
                    f"{chunk.chunk_number}."
                ),
            )

        chunk_data: bytes | None = None

        for replica in replicas:
            storage_node = db.get(
                StorageNode,
                replica.storage_node_id,
            )

            if storage_node is None:
                continue

            storage = LocalStorageBackend(
                settings.local_storage_root,
                node_id=storage_node.node_id,
            )

            try:
                candidate_data = storage.get(
                    replica.storage_key
                )
            except FileNotFoundError:
                continue

            if sha256(candidate_data).hexdigest() != chunk.checksum:
                continue

            chunk_data = candidate_data
            break

        if chunk_data is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "Stored upload content failed "
                    "integrity verification."
                ),
            )

        content_parts.append(chunk_data)

    data = b"".join(content_parts)

    if len(data) != upload_session.total_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Uploaded content size does not match the expected "
                f"size of {upload_session.total_size_bytes} bytes."
            ),
        )

    checksum = sha256(data).hexdigest()

    latest_version_number = db.scalar(
        select(FileVersion.version_number)
        .where(
            FileVersion.file_id == file.id,
            FileVersion.version_number >= 0,
        )
        .order_by(FileVersion.version_number.desc())
        .limit(1)
    )

    final_version_number = (latest_version_number or 0) + 1

    temporary_version.version_number = final_version_number
    temporary_version.size_bytes = len(data)
    temporary_version.checksum = checksum

    file.name = upload_session.filename
    file.mime_type = upload_session.mime_type
    file.size_bytes = len(data)
    file.current_version_id = temporary_version.id

    upload_session.status = "completed"
    upload_session.completed_at = datetime.now(timezone.utc)

    db.commit()

    db.refresh(file)
    db.refresh(upload_session)

    return CompleteUploadResponse(
        file=file,
        session=serialize_upload_session(upload_session),

    )
# ---------------------------------------------------------------------------
# File versions
# ---------------------------------------------------------------------------


@api_router.get(
    "/files/{file_id}/versions",
    response_model=FileVersionListResponse,
    tags=["versions"],
)
def list_file_versions(
    file_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileVersionListResponse:
    file = db.scalar(
        select(File).where(
            File.id == file_id,
            File.owner_id == current_user.id,
        )
    )
    if file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found.",
        )

    versions = list(
        db.scalars(
            select(FileVersion)
            .where(
                FileVersion.file_id == file.id,
                FileVersion.version_number >= 0,
            )
            .order_by(FileVersion.version_number.asc())
        ).all()
    )

    return FileVersionListResponse(
        versions=[
            FileVersionResponse.model_validate(version)
            for version in versions
        ]
    )


@api_router.get(
    "/files/{file_id}/versions/{version_number}",
    response_model=FileVersionResponse,
    tags=["versions"],
)
def get_file_version(
    file_id: int,
    version_number: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileVersionResponse:
    file = db.scalar(
        select(File).where(
            File.id == file_id,
            File.owner_id == current_user.id,
        )
    )
    if file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found.",
        )

    version = db.scalar(
        select(FileVersion).where(
            FileVersion.file_id == file.id,
            FileVersion.version_number == version_number,
        )
    )
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File version not found.",
        )

    return FileVersionResponse.model_validate(version)


@api_router.get(
    "/files/{file_id}/versions/{version_number}/content",
    tags=["versions"],
)
def download_file_version_content(
    file_id: int,
    version_number: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    file = db.scalar(
        select(File).where(
            File.id == file_id,
            File.owner_id == current_user.id,
        )
    )
    if file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found.",
        )

    version = db.scalar(
        select(FileVersion).where(
            FileVersion.file_id == file.id,
            FileVersion.version_number == version_number,
        )
    )
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File version not found.",
        )

    chunks = list(
        db.scalars(
            select(Chunk)
            .where(Chunk.version_id == version.id)
            .order_by(Chunk.chunk_number)
        ).all()
    )

    if not chunks:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File version content not found.",
        )

    content_parts: list[bytes] = []

    for chunk in chunks:
        replicas = get_healthy_chunk_replicas(
            db,
            chunk.id
        )

        if not replicas:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"No healthy replica exists for chunk "
                    f"{chunk.chunk_number}."
                ),
            )

        chunk_data: bytes | None = None

        for replica in replicas:
            storage_node = db.get(
                StorageNode,
                replica.storage_node_id,
            )

            if storage_node is None:
                continue

            storage = LocalStorageBackend(
                settings.local_storage_root,
                node_id=storage_node.node_id,
            )

            try:
                candidate_data = storage.get(
                    replica.storage_key
                )
            except FileNotFoundError:
                continue

            if sha256(candidate_data).hexdigest() != chunk.checksum:
                continue

            chunk_data = candidate_data
            break

        if chunk_data is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "Stored file version content failed "
                    "integrity verification."
                ),
            )

        content_parts.append(chunk_data)

    data = b"".join(content_parts)

    if len(data) != version.size_bytes:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored file version size does not match metadata.",
        )

    if sha256(data).hexdigest() != version.checksum:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored file version failed integrity verification.",
        )

    return Response(
        content=data,
        media_type=file.mime_type or "application/octet-stream",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{file.name}"'
            ),
        },
    )
# ---------------------------------------------------------------------------
# Secure sharing
# ---------------------------------------------------------------------------


@api_router.post(
    "/files/{file_id}/shares",
    response_model=ShareCreateResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["shares"],
)
def create_file_share(
    file_id: int,
    share_request: ShareCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ShareCreateResponse:
    file = db.scalar(
        select(File).where(
            File.id == file_id,
            File.owner_id == current_user.id,
        )
    )

    if file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found.",
        )

    if share_request.permission not in {"viewer", "editor"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported share permission.",
        )

    if (
        share_request.expires_at is not None
        and share_request.expires_at <= datetime.now(timezone.utc)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Share expiration must be in the future.",
        )

    token = secrets.token_urlsafe(32)
    token_hash = sha256(token.encode("utf-8")).hexdigest()

    share = Share(
        file_id=file.id,
        owner_id=current_user.id,
        token_hash=token_hash,
        permission=share_request.permission,
        expires_at=share_request.expires_at,
    )

    db.add(share)
    db.commit()
    db.refresh(share)

    return ShareCreateResponse(
        share=ShareResponse.model_validate(share),
        token=token,
    )
# ---------------------------------------------------------------------------
# Public share access
# ---------------------------------------------------------------------------


@api_router.get(
    "/shares/{token}",
    response_model=ShareResponse,
    tags=["shares"],
)
def get_shared_file(
    token: str,
    db: Session = Depends(get_db),
) -> ShareResponse:
    token_hash = sha256(
        token.encode("utf-8")
    ).hexdigest()

    share = db.scalar(
        select(Share).where(
            Share.token_hash == token_hash,
        )
    )

    if share is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share not found.",
        )

    now = datetime.now(timezone.utc)

    if share.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share not found.",
        )

    if (
        share.expires_at is not None
        and share.expires_at <= now
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share not found.",
        )

    return ShareResponse.model_validate(share)
@api_router.delete(
    "/files/{file_id}/shares/{share_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["shares"],
)
def revoke_file_share(
    file_id: int,
    share_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    share = db.scalar(
        select(Share).where(
            Share.id == share_id,
            Share.file_id == file_id,
            Share.owner_id == current_user.id,
        )
    )

    if share is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share not found.",
        )

    if share.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share not found.",
        )

    share.revoked_at = datetime.now(timezone.utc)

    db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
@api_router.get(
    "/shares/{token}/content",
    tags=["shares"],
)
def download_shared_file_content(
    token: str,
    db: Session = Depends(get_db),
):
    token_hash = sha256(
        token.encode("utf-8")
    ).hexdigest()

    share = db.scalar(
        select(Share).where(
            Share.token_hash == token_hash,
        )
    )

    if share is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share not found.",
        )

    now = datetime.now(timezone.utc)

    if share.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share not found.",
        )

    if (
        share.expires_at is not None
        and share.expires_at <= now
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share not found.",
        )

    file = db.scalar(
        select(File).where(
            File.id == share.file_id,
        )
    )

    if file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found.",
        )

    if file.current_version_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File content not found.",
        )

    chunks = list(
        db.scalars(
            select(Chunk)
            .where(
                Chunk.version_id == file.current_version_id
            )
            .order_by(Chunk.chunk_number)
        ).all()
    )

    if not chunks:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File content not found.",
        )

    content_parts: list[bytes] = []

    for chunk in chunks:
        replicas = get_healthy_chunk_replicas(
            db,
            chunk.id,
        )

        if not replicas:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"No healthy replica exists for chunk "
                    f"{chunk.chunk_number}."
                ),
            )

        chunk_data: bytes | None = None

        for replica in replicas:
            storage_node = db.get(
                StorageNode,
                replica.storage_node_id,
            )

            if storage_node is None:
                continue

            storage = LocalStorageBackend(
                settings.local_storage_root,
                node_id=storage_node.node_id,
            )

            try:
                candidate_data = storage.get(
                    replica.storage_key
                )
            except FileNotFoundError:
                continue

            if sha256(candidate_data).hexdigest() != chunk.checksum:
                continue

            chunk_data = candidate_data
            break

        if chunk_data is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "Stored shared file content failed "
                    "integrity verification."
                ),
            )

        content_parts.append(chunk_data)

    data = b"".join(content_parts)

    version = db.scalar(
        select(FileVersion).where(
            FileVersion.id == file.current_version_id,
        )
    )

    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File version not found.",
        )

    if len(data) != version.size_bytes:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored shared file size does not match metadata.",
        )

    if sha256(data).hexdigest() != version.checksum:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored shared file failed integrity verification.",
        )

    return Response(
        content=data,
        media_type=file.mime_type or "application/octet-stream",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{file.name}"'
            ),
        },
    )