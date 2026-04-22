from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AuthRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=40)


class ProfileUpdateRequest(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=32)
    display_name: str | None = Field(default=None, max_length=40)
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str | None = Field(default=None, min_length=8, max_length=128)


class UserRead(BaseModel):
    id: int
    username: str
    display_name: str
    initials: str
    avatar_url: str | None = None
    created_at: datetime


class UserSearchRead(BaseModel):
    username: str
    display_name: str
    initials: str
    avatar_url: str | None = None
    is_friend: bool
    request_state: str
    is_online: bool


class SessionRead(BaseModel):
    authenticated: bool
    user: UserRead | None
    app_name: str


class FriendRequestCreate(BaseModel):
    username: str = Field(min_length=3, max_length=32)


class FriendRequestRead(BaseModel):
    id: int
    requester: UserRead
    addressee: UserRead
    status: str
    created_at: datetime
    responded_at: datetime | None


class FriendRead(BaseModel):
    username: str
    display_name: str
    initials: str
    avatar_url: str | None = None
    is_online: bool
    last_message: str | None
    last_message_at: datetime | None
    unread_count: int


class DirectMessageRead(BaseModel):
    id: int
    sender_username: str
    sender_display_name: str
    content: str
    attachment_name: str | None = None
    attachment_url: str | None = None
    attachment_size: int | None = None
    attachment_mime_type: str | None = None
    sent_at: datetime


class HealthRead(BaseModel):
    status: str
    database_backend: str
