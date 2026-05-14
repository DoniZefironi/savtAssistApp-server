from typing import Generic, TypeVar
from pydantic import BaseModel

T = TypeVar("T")


class PageOut(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    size: int
    pages: int


def make_page(items: list, total: int, page: int, size: int) -> PageOut:
    return PageOut(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=max(1, (total + size - 1) // size),
    )
