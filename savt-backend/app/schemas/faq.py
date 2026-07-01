from datetime import datetime
from pydantic import BaseModel, Field


class FaqCategoryCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    parent_id: int | None = Field(None, gt=0)
    sort_order: int = Field(0, ge=0)


class FaqCategoryUpdateIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    parent_id: int | None = Field(None, gt=0)
    sort_order: int | None = Field(None, ge=0)


class FaqCategoryOut(BaseModel):
    id: int
    parent_id: int | None
    name: str
    sort_order: int

    model_config = {"from_attributes": True}


class FaqEntryCreateIn(BaseModel):
    category_id: int = Field(..., gt=0)
    question: str = Field(..., min_length=5, max_length=2000)
    answer: str = Field(..., min_length=1, max_length=10000)


class FaqEntryUpdateIn(BaseModel):
    category_id: int | None = Field(None, gt=0)
    question: str | None = Field(None, min_length=5, max_length=2000)
    answer: str | None = Field(None, min_length=1, max_length=10000)
    is_published: bool | None = None


class FaqEntryOut(BaseModel):
    id: int
    category_id: int
    question: str
    answer: str
    version: int
    is_published: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
