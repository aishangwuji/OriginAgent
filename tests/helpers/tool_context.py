from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from OpenHome.agent.tools.file_state import FileStates, bind_file_states, reset_file_states


@contextmanager
def bound_file_states() -> Iterator[FileStates]:
    states = FileStates()
    token = bind_file_states(states)
    try:
        yield states
    finally:
        reset_file_states(token)
