"""PostgreSQL text projections with Python's whitespace semantics."""

from sqlalchemy import ColumnElement, func


# str.strip() includes Unicode whitespace and the ASCII record separators.
PYTHON_STRIP_CHARACTERS = (
    "\t\n\v\f\r\x1c\x1d\x1e\x1f \u0085\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)


def stripped_text(column: ColumnElement[str | None]) -> ColumnElement[str | None]:
    return func.btrim(column, PYTHON_STRIP_CHARACTERS)


def preview_text(column: ColumnElement[str | None], limit: int) -> ColumnElement[str | None]:
    """Retain one extra character so the renderer can disclose truncation."""
    return func.substr(stripped_text(column), 1, limit + 1)
