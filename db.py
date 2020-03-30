from contextlib import contextmanager

from sqlalchemy import Column, Integer, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

engine = create_engine("sqlite:///db.sqlite3")
_SessionFactory = sessionmaker(bind=engine)
Base = declarative_base()


def session_factory():
    Base.metadata.create_all(engine)
    return _SessionFactory()


@contextmanager
def session_scope():
    """Provide a transactional scope around a series of operations."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except:
        session.rollback()
        raise
    finally:
        session.close()


class Link(Base):
    __tablename__ = "link"

    id = Column(Integer, primary_key=True)
    discord_id = Column(Integer, unique=True)
    academy_id = Column(Integer, unique=True)
