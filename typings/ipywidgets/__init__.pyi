"""Typed subset of the public ipywidgets API used by the notebook.

ipywidgets 8 does not ship typing metadata. These declarations describe the
constructors and interactive-output contract used here; no runtime code changes.
"""
from collections.abc import Callable, Mapping, Sequence

class Widget:
    def observe(self, handler: Callable[[Mapping[str, object]], object], names: str = ...) -> None: ...

class ValueWidget[T](Widget):
    value: T

class Output(Widget): ...

class Checkbox(ValueWidget[bool]):
    def __init__(self, *, value: bool = ..., description: str = ..., indent: bool = ...) -> None: ...

class Dropdown[T](ValueWidget[T]):
    def __init__(self, *, options: Sequence[T] | Sequence[tuple[str, T]], value: T = ..., description: str = ...) -> None: ...

class Text(ValueWidget[str]):
    def __init__(self, *, description: str = ..., placeholder: str = ..., continuous_update: bool = ...) -> None: ...

class Combobox(ValueWidget[str]):
    def __init__(self, *, options: Sequence[str], value: str = ..., description: str = ..., ensure_option: bool = ...) -> None: ...

class IntSlider(ValueWidget[int]):
    def __init__(self, *, value: int = ..., min: int = ..., max: int = ..., step: int = ..., description: str = ..., continuous_update: bool = ...) -> None: ...

class HBox(Widget):
    def __init__(self, children: Sequence[Widget] = ...) -> None: ...

class VBox(Widget):
    def __init__(self, children: Sequence[Widget] = ...) -> None: ...

def interactive_output(f: Callable[..., object], controls: Mapping[str, Widget]) -> Output: ...
