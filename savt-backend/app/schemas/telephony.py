from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ObjectKind = Literal["project", "cabinet"]


class ResolveObjectIn(BaseModel):
    number: str = Field(min_length=1, max_length=100, strict=True)
    kind: ObjectKind | None = None


class ObjectMatchOut(BaseModel):
    kind: ObjectKind
    cabinet_id: int | None
    cabinet_number: str | None
    project_id: int | None
    project_number: str | None
    bitrix_deal_id: str | None


class ResolveObjectOut(BaseModel):
    status: Literal["found", "ambiguous", "not_found"]
    match: ObjectMatchOut | None = None


class AnswerQuestionIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=2000, strict=True)


class AnswerQuestionOut(BaseModel):
    status: Literal["answered", "needs_operator"]
    answer: str | None = None
