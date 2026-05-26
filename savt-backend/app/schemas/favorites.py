from datetime import datetime
from pydantic import BaseModel, Field


class FavoriteIn(BaseModel):
    entity_type: str = Field(..., pattern="^(document|kb_article|faq_entry)$")
    entity_id: int = Field(..., gt=0)


class FavoriteOut(BaseModel):
    id: int
    entity_type: str
    entity_id: int
    created_at: datetime

    model_config = {"from_attributes": True}
