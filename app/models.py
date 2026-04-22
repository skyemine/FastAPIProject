from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(40))
    password_hash: Mapped[str] = mapped_column(String(255))
    avatar_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_mime_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    avatar_token: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    sent_requests: Mapped[list["FriendRequest"]] = relationship(
        foreign_keys="FriendRequest.requester_id",
        back_populates="requester",
        cascade="all, delete-orphan",
    )
    received_requests: Mapped[list["FriendRequest"]] = relationship(
        foreign_keys="FriendRequest.addressee_id",
        back_populates="addressee",
        cascade="all, delete-orphan",
    )
    friendships: Mapped[list["Friendship"]] = relationship(
        foreign_keys="Friendship.user_id",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    messages_sent: Mapped[list["DirectMessage"]] = relationship(
        foreign_keys="DirectMessage.sender_id",
        back_populates="sender",
        cascade="all, delete-orphan",
    )
    messages_received: Mapped[list["DirectMessage"]] = relationship(
        foreign_keys="DirectMessage.recipient_id",
        back_populates="recipient",
        cascade="all, delete-orphan",
    )
    auth_sessions: Mapped[list["AuthSession"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    push_subscriptions: Mapped[list["PushSubscription"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class FriendRequest(Base):
    __tablename__ = "friend_requests"
    __table_args__ = (
        UniqueConstraint("requester_id", "addressee_id", name="uq_friend_request_pair"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    addressee_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    requester: Mapped[User] = relationship(foreign_keys=[requester_id], back_populates="sent_requests")
    addressee: Mapped[User] = relationship(foreign_keys=[addressee_id], back_populates="received_requests")


class Friendship(Base):
    __tablename__ = "friendships"
    __table_args__ = (
        UniqueConstraint("user_id", "friend_id", name="uq_friendship_pair"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    friend_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    user: Mapped[User] = relationship(foreign_keys=[user_id], back_populates="friendships")
    friend: Mapped[User] = relationship(foreign_keys=[friend_id])


class DirectMessage(Base):
    __tablename__ = "direct_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    recipient_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text(), default="")
    attachment_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attachment_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attachment_size: Mapped[int | None] = mapped_column(nullable=True)
    attachment_mime_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    attachment_token: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)

    sender: Mapped[User] = relationship(foreign_keys=[sender_id], back_populates="messages_sent")
    recipient: Mapped[User] = relationship(foreign_keys=[recipient_id], back_populates="messages_received")


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(120), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    user: Mapped[User] = relationship(back_populates="auth_sessions")


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    p256dh: Mapped[str] = mapped_column(String(255))
    auth: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    user: Mapped[User] = relationship(back_populates="push_subscriptions")
