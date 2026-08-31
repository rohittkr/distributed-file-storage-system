from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FileCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    mime_type: str | None = Field(default=None, max_length=255)


class FileUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=512)
    mime_type: str | None = Field(default=None, max_length=255)


class FileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    mime_type: str | None
    size_bytes: int
    current_version_id: int | None
    created_at: datetime
    updated_at: datetime


class UploadSessionCreateRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=512)
    total_size_bytes: int = Field(ge=0)
    mime_type: str | None = Field(default=None, max_length=255)


class UploadSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    mime_type: str | None
    total_size_bytes: int
    chunk_size_bytes: int
    total_chunks: int
    received_chunks: int
    status: str
    file_id: int | None
    created_at: datetime
    expires_at: datetime
    completed_at: datetime | None


class UploadChunkResponse(BaseModel):
    session_id: int
    chunk_number: int
    size_bytes: int
    received_chunks: int
    total_chunks: int
    status: str


class CompleteUploadResponse(BaseModel):
    file: FileResponse
    session: UploadSessionResponse


class StorageNodeCreateRequest(BaseModel):
    node_id: str = Field(min_length=1, max_length=128)
    endpoint: str = Field(min_length=1, max_length=512)
    capacity_bytes: int = Field(ge=0)


class StorageNodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    node_id: str
    endpoint: str
    status: str
    capacity_bytes: int
    used_bytes: int
    last_heartbeat: datetime | None


class StorageNodeHeartbeatResponse(BaseModel):
    id: int
    node_id: str
    status: str
    last_heartbeat: datetime
class FileVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    file_id: int
    version_number: int
    size_bytes: int
    checksum: str
    created_at: datetime


class FileVersionListResponse(BaseModel):
    versions: list[FileVersionResponse]
