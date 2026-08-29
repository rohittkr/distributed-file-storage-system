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