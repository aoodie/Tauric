from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from .config import runtime_config


@lru_cache
def engine():
    return create_engine(
        runtime_config().database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=2,
        connect_args={"connect_timeout": 3},
    )


def database_session() -> Generator[Session]:
    with Session(engine()) as session:
        yield session
