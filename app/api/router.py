from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models.file import File
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

api_router = APIRouter()


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

    existing_user = db.scalar(select(User).where(User.email == email))
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

    user = db.scalar(select(User).where(User.email == email))

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

    if not verify_password(payload.password, user.password_hash):
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