from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator

from ...schemas import ResumeParseResponse

MAX_BYTES = 5 * 1024 * 1024
MEDIA_TYPES = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


class UploadCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: StrictStr = Field(min_length=1, max_length=255)
    size_bytes: StrictInt = Field(ge=1, le=MAX_BYTES)
    sha256: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    enrich_skills: StrictBool = False

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        name = value.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not name or len(name.encode("utf-8")) > 255 or any(ord(c) < 32 or ord(c) == 127 for c in name):
            raise ValueError("Invalid resume filename")
        if name.rsplit(".", 1)[-1].lower() not in {"pdf", "docx"}:
            raise ValueError("Direct safe upload currently supports PDF and DOCX")
        return name


class UploadIntent(BaseModel):
    upload_id: str
    state: str
    upload_url: str | None = None
    method: Literal["PUT"] = "PUT"
    headers: dict[str, str] = Field(default_factory=dict)
    upload_grant_expires_at: datetime
    expires_at: datetime


class UploadStatus(BaseModel):
    upload_id: str
    state: str
    error_code: str | None = None
    resume: ResumeParseResponse | None = None


class SourceAccess(BaseModel):
    url: str
    expires_at: datetime
    sha256: str
    size_bytes: int
    filename: str
    media_type: str
