import uuid

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    metadata = MetaData()


def gen_uuid() -> uuid.UUID:
    return uuid.uuid4()
