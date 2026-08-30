from hashlib import sha256

from fastapi import (
    APIRouter,
    Depends,
    File as FastAPIFile,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models.file import Chunk, ChunkReplica, File, FileVersion, StorageNode
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.schemas.files import (
    FileCreateRequest,
    FileResponse,
    FileUpdateRequest,
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


@api_router.get("/version")
def version() -> dict[str, str]:
    return {"version": "0.1.0", "status": "foundation"}


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

    return file


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

    storage = LocalStorageBackend(
        settings.local_storage_root
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
                    f"Not enough healthy storage nodes for the required "
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

    storage = LocalStorageBackend(
        settings.local_storage_root
    )

    content_parts: list[bytes] = []

    for chunk in chunks:
        replicas = list(
            db.scalars(
                select(ChunkReplica)
                .where(
                    ChunkReplica.chunk_id == chunk.id,
                    ChunkReplica.status == "healthy",
                )
                .order_by(ChunkReplica.id)
            ).all()
        )

        if not replicas:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File content not found.",
            )

        chunk_data: bytes | None = None

        for replica in replicas:
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