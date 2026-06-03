from pydantic import BaseModel, Field


class TagOut(BaseModel):
    id: int
    name: str
    scope: str

    model_config = {"from_attributes": True}


class TagCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    scope: str = Field("document", pattern="^(document|cabinet)$")


class DocumentTagsIn(BaseModel):
    tag_ids: list[int] = Field(default=[], description="Пустой список снимает все теги")
