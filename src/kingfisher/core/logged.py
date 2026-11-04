"""What kingfisher's logs may say of a failure."""

from __future__ import annotations

from pathlib import PurePath


def raised(exc: BaseException) -> tuple[str, str | None]:
    """`exc` as its class's name and where it was raised, `folder/file.py:LINE in FUNC`,
    or `None` for one never raised.

    Never its message, which is content: a tool's `ValidationError` quotes the argument
    the model sent, and a `FileNotFoundError` the path.
    """
    trace = exc.__traceback__
    if trace is None:
        return type(exc).__name__, None
    while trace.tb_next is not None:
        trace = trace.tb_next
    code = trace.tb_frame.f_code
    where = "/".join(PurePath(code.co_filename).parts[-2:])
    return type(exc).__name__, f"{where}:{trace.tb_lineno} in {code.co_name}"
