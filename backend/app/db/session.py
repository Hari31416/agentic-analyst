from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def session_factory() -> sessionmaker[Session]:
    settings = get_settings()
    engine = create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3},
    )
    return sessionmaker(engine, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    # Imported lazily so liveness does not depend on an available database.
    with factory()() as session:
        yield session


from functools import lru_cache

factory = lru_cache(maxsize=1)(session_factory)
