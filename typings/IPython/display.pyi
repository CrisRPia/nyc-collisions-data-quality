"""Typed public display operations used by the notebook."""
from collections.abc import Mapping, Sequence

class DisplayHandle: ...

def update_display(obj: object, *, display_id: str) -> None: ...

class HTML:
    data: str
    def __init__(self, data: str = ..., url: str | None = ..., filename: str | None = ..., metadata: Mapping[str, object] | None = ...) -> None: ...

class Markdown:
    def __init__(self, data: str = ..., url: str | None = ..., filename: str | None = ..., metadata: Mapping[str, object] | None = ...) -> None: ...

class FileLink:
    def __init__(self, path: str, url_prefix: str = ..., result_html_prefix: str = ..., result_html_suffix: str = ...) -> None: ...

def display(
    *objs: object, include: Sequence[str] | None = ..., exclude: Sequence[str] | None = ...,
    metadata: Mapping[str, object] | None = ..., transient: Mapping[str, object] | None = ...,
    display_id: str | bool | None = ..., raw: bool = ..., clear: bool = ..., **kwargs: object,
) -> DisplayHandle | None: ...
