from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """모든 ORM 모델의 부모. Alembic이 metadata target으로 참조."""

    pass
