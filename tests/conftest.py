"""Empty integration selection is success until later cards add Docker checks."""

from __future__ import annotations

import pytest


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if exitstatus == 5 and session.config.option.markexpr == "integration":
        session.exitstatus = 0
