LIKE_ESCAPE_CHAR = "\\"


def escape_like(value: str) -> str:
    """Escape LIKE/ILIKE wildcards (%, _) and the escape character itself."""
    return (
        value.replace(LIKE_ESCAPE_CHAR, LIKE_ESCAPE_CHAR * 2)
        .replace("%", f"{LIKE_ESCAPE_CHAR}%")
        .replace("_", f"{LIKE_ESCAPE_CHAR}_")
    )
