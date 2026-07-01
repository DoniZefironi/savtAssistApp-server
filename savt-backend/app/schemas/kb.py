from datetime import datetime
from pydantic import BaseModel, Field

from app.schemas.tags import TagOut


class KbCategoryCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    parent_id: int | None = Field(None, gt=0)
    description: str | None = Field(None, max_length=2000)
    sort_order: int = Field(0, ge=0)


class KbCategoryUpdateIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    parent_id: int | None = Field(None, gt=0)
    description: str | None = Field(None, max_length=2000)
    sort_order: int | None = Field(None, ge=0)


class KbCategoryOut(BaseModel):
    id: int
    parent_id: int | None
    name: str
    slug: str
    description: str | None
    sort_order: int

    model_config = {"from_attributes": True}


class KbAttachmentOut(BaseModel):
    id: int
    article_id: int
    file_url: str
    file_size_bytes: int
    doc_type: str
    mime_type: str
    title: str
    created_at: datetime

    model_config = {"from_attributes": True}


class KbArticleCreateIn(BaseModel):
    category_id: int = Field(..., gt=0)
    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = Field(None, max_length=5000)


class KbArticleUpdateIn(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=500)
    description: str | None = Field(None, max_length=5000)
    category_id: int | None = Field(None, gt=0)
    is_published: bool | None = None


class KbArticleListOut(BaseModel):
    id: int
    category_id: int
    title: str
    slug: str
    description: str | None
    version: int
    is_published: bool
    created_at: datetime
    tags: list[TagOut] = []
    attachment_count: int = 0


class KbArticleDetailOut(BaseModel):
    id: int
    category_id: int
    title: str
    slug: str
    description: str | None
    version: int
    is_published: bool
    created_at: datetime
    updated_at: datetime
    tags: list[TagOut] = []
    attachments: list[KbAttachmentOut] = []
