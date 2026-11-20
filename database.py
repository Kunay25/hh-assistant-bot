"""Пользователи и история диалогов в PostgreSQL через SQLAlchemy ORM."""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from config import DATABASE_URL


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("platform", "platform_user_id", name="uq_user_platform"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    platform_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Interaction(Base):
    __tablename__ = "interactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    user_query: Mapped[str] = mapped_column(Text, nullable=False)
    bot_response: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


if not DATABASE_URL:
    raise RuntimeError(
        "В файле .env не задан DATABASE_URL. "
        "Установите PostgreSQL, создайте БД hh_bot_db и задайте строку подключения."
    )

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)


def init_db() -> None:
    """Создаёт таблицы, если их ещё нет. Саму базу создают заранее в pgAdmin."""
    Base.metadata.create_all(engine)


def _user_dict(user: User) -> Dict[str, Any]:
    return {
        "id": user.id,
        "platform": user.platform,
        "platform_user_id": user.platform_user_id,
        "username": user.username,
        "first_name": user.first_name,
        "created_at": user.created_at,
        "last_seen": user.last_seen,
    }


def get_or_create_user(
    platform: str,
    platform_user_id: str,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
) -> Dict[str, Any]:
    platform_user_id = str(platform_user_id)
    with SessionLocal() as session:
        user = session.scalar(
            select(User).where(
                User.platform == platform,
                User.platform_user_id == platform_user_id,
            )
        )
        if user is None:
            user = User(
                platform=platform,
                platform_user_id=platform_user_id,
                username=username or "",
                first_name=first_name or "",
            )
            session.add(user)
        else:
            user.last_seen = datetime.utcnow()
            if username is not None:
                user.username = username
            if first_name is not None:
                user.first_name = first_name
        session.commit()
        session.refresh(user)
        return _user_dict(user)


def save_interaction(
    user_id: int,
    user_query: str,
    bot_response: str,
    sources: Optional[str] = "",
) -> int:
    with SessionLocal() as session:
        item = Interaction(
            user_id=user_id,
            user_query=user_query,
            bot_response=bot_response,
            sources=sources or "",
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return item.id


def get_user_history(user_id: int, limit: int = 5) -> List[Dict[str, Any]]:
    with SessionLocal() as session:
        items = session.scalars(
            select(Interaction)
            .where(Interaction.user_id == user_id)
            .order_by(Interaction.created_at.desc(), Interaction.id.desc())
            .limit(limit)
        ).all()
        return [
            {
                "id": item.id,
                "user_query": item.user_query,
                "bot_response": item.bot_response,
                "sources": item.sources,
                "created_at": str(item.created_at),
            }
            for item in items
        ]


def get_database_stats() -> Dict[str, Any]:
    with SessionLocal() as session:
        total_users = session.scalar(select(func.count()).select_from(User)) or 0
        total_interactions = session.scalar(select(func.count()).select_from(Interaction)) or 0
        rows = session.execute(
            select(User.platform, func.count()).group_by(User.platform)
        ).all()
        return {
            "total_users": total_users,
            "total_interactions": total_interactions,
            "platforms": {platform: count for platform, count in rows},
        }
