from __future__ import annotations

import asyncio
import base64
import json
import re
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypeVar

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import and_, delete, inspect as sa_inspect, or_, select, text
from sqlalchemy.orm import Session, aliased, selectinload
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from .chat import ConnectionManager
from .config import Settings, load_settings
from .database import Database
from .middleware import SecurityHeadersMiddleware
from .models import AuthSession, DirectMessage, FriendRequest, Friendship, PushSubscription, User
from .schemas import (
    AuthRequest,
    DirectMessageRead,
    FriendRead,
    FriendRequestCreate,
    FriendRequestRead,
    HealthRead,
    ProfileUpdateRequest,
    PushSubscriptionCreate,
    SessionRead,
    UserRead,
    UserSearchRead,
)
from .security import (
    InvalidSessionError,
    RateLimitError,
    SessionManager,
    burn_password_check,
    hash_password,
    initials_for_name,
    parse_isoformat,
    rate_limiter,
    validate_password_strength,
    verify_password,
)

try:
    from pywebpush import WebPushException, webpush
except Exception:  # pragma: no cover - optional dependency at runtime
    WebPushException = Exception
    webpush = None

T = TypeVar("T")
USERNAME_PATTERN = re.compile(r"[^a-z0-9_]+")
MAX_ATTACHMENT_SIZE = 25 * 1024 * 1024
MAX_AVATAR_SIZE = 5 * 1024 * 1024
INLINE_ATTACHMENT_TYPES = {
    "image/jpeg",
    "image/gif",
    "image/png",
    "image/webp",
    "video/mp4",
    "video/webm",
    "application/pdf",
    "application/json",
    "text/css",
    "text/csv",
    "text/html",
    "text/javascript",
    "text/markdown",
    "text/plain",
    "text/x-python",
    "application/zip",
    "application/x-zip-compressed",
}
ALLOWED_AVATAR_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


@dataclass(slots=True)
class UserIdentity:
    id: int
    username: str
    display_name: str
    avatar_token: str | None
    created_at: str


def direct_channel_key(user_a: int, user_b: int) -> str:
    ordered = sorted((user_a, user_b))
    return f"dm:{ordered[0]}:{ordered[1]}"


def updates_channel_key(user_id: int) -> str:
    return f"user:{user_id}"


def normalize_username(value: str) -> str:
    normalized = USERNAME_PATTERN.sub("", value.strip().lower())
    if len(normalized) < 3:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Username must contain at least 3 latin letters, digits, or underscores.",
        )
    return normalized[:32]


def normalize_display_name(value: str, fallback: str) -> str:
    cleaned = " ".join(value.strip().split())
    return (cleaned or fallback)[:40]


def run_with_session(database: Database, callback: Callable[[Session], T]) -> T:
    with database.session() as session:
        return callback(session)


def detect_database_backend(database_url: str) -> str:
    if database_url.startswith("sqlite"):
        return "sqlite"
    if database_url.startswith("postgresql"):
        return "postgresql"
    return database_url.split(":", maxsplit=1)[0]


def user_to_identity(user: User) -> UserIdentity:
    return UserIdentity(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        avatar_token=user.avatar_token,
        created_at=user.created_at.isoformat(),
    )


def avatar_url_for(user: User | UserIdentity) -> str | None:
    token = getattr(user, "avatar_token", None)
    if not token:
        return None
    return f"/api/avatars/{token}"


def normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def identity_to_user_schema(identity: UserIdentity) -> UserRead:
    return UserRead(
        id=identity.id,
        username=identity.username,
        display_name=identity.display_name,
        initials=initials_for_name(identity.display_name),
        avatar_url=avatar_url_for(identity),
        created_at=parse_isoformat(identity.created_at),
    )


def user_to_schema(user: User) -> UserRead:
    return UserRead(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        initials=initials_for_name(user.display_name),
        avatar_url=avatar_url_for(user),
        created_at=normalize_datetime(user.created_at),
    )


def friend_request_to_schema(request_obj: FriendRequest) -> FriendRequestRead:
    return FriendRequestRead(
        id=request_obj.id,
        requester=user_to_schema(request_obj.requester),
        addressee=user_to_schema(request_obj.addressee),
        status=request_obj.status,
        created_at=normalize_datetime(request_obj.created_at),
        responded_at=normalize_datetime(request_obj.responded_at) if request_obj.responded_at else None,
    )


def direct_message_to_schema(message: DirectMessage) -> DirectMessageRead:
    attachment_url = f"/api/files/{message.attachment_token}" if message.attachment_token else None
    return DirectMessageRead(
        id=message.id,
        sender_username=message.sender.username,
        sender_display_name=message.sender.display_name,
        content=message.content,
        attachment_name=message.attachment_name,
        attachment_url=attachment_url,
        attachment_size=message.attachment_size,
        attachment_mime_type=message.attachment_mime_type,
        sent_at=normalize_datetime(message.sent_at),
    )


def load_identity(session: Session, user_id: int) -> UserIdentity | None:
    user = session.get(User, user_id)
    if user is None:
        return None
    return user_to_identity(user)


def create_user(session: Session, payload: AuthRequest) -> UserIdentity:
    username = normalize_username(payload.username)
    display_name = normalize_display_name(payload.display_name or username, username)
    try:
        validate_password_strength(payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    existing_user = session.scalar(select(User).where(User.username == username))
    if existing_user is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username is already taken.")

    user = User(username=username, display_name=display_name, password_hash=hash_password(payload.password))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user_to_identity(user)


def authenticate_user(session: Session, payload: AuthRequest) -> UserIdentity:
    username = normalize_username(payload.username)
    user = session.scalar(select(User).where(User.username == username))
    if user is None:
        burn_password_check(payload.password)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password.")
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password.")
    return user_to_identity(user)


def update_profile(session: Session, current_user_id: int, payload: ProfileUpdateRequest) -> UserRead:
    user = session.get(User, current_user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User session is invalid.")
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect.")

    if payload.username is not None and payload.username.strip():
        normalized_username = normalize_username(payload.username)
        if normalized_username != user.username:
            existing_user = session.scalar(select(User).where(User.username == normalized_username))
            if existing_user is not None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username is already taken.")
            user.username = normalized_username

    if payload.display_name is not None:
        user.display_name = normalize_display_name(payload.display_name, user.username)

    if payload.new_password:
        try:
            validate_password_strength(payload.new_password)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        user.password_hash = hash_password(payload.new_password)

    session.commit()
    session.refresh(user)
    return user_to_schema(user)


def extract_client_key(request: Request, username: str | None = None) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        ip_address = forwarded_for.split(",")[0].strip()
    else:
        ip_address = request.client.host if request.client else "unknown"
    if username:
        return f"{ip_address}:{normalize_username(username)}"
    return ip_address


def extract_ip_and_user_agent(request: Request) -> tuple[str, str]:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    ip_address = forwarded_for.split(",")[0].strip() if forwarded_for else (request.client.host if request.client else "unknown")
    user_agent = request.headers.get("user-agent", "").strip()[:255]
    return ip_address or "unknown", user_agent


def push_enabled(settings: Settings) -> bool:
    return bool(settings.push_public_key and settings.push_private_key and webpush is not None)


def vapid_claims(settings: Settings) -> dict[str, str]:
    return {"sub": settings.push_subject}


def base64url_to_bytes(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}")


def bytes_to_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def ensure_push_configuration(settings: Settings) -> None:
    if settings.push_public_key and settings.push_private_key:
        return

    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
    except Exception:
        return

    push_dir = Path(settings.storage_dir) / "data" / "push"
    private_key_path = push_dir / "vapid_private.pem"
    public_key_path = push_dir / "vapid_public.txt"

    if private_key_path.exists() and public_key_path.exists():
        settings.push_private_key = str(private_key_path.resolve())
        settings.push_public_key = public_key_path.read_text(encoding="utf-8").strip()
        return

    push_dir.mkdir(parents=True, exist_ok=True)
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    private_key_path.write_bytes(private_bytes)
    public_key_path.write_text(bytes_to_base64url(public_bytes), encoding="utf-8")
    settings.push_private_key = str(private_key_path.resolve())
    settings.push_public_key = public_key_path.read_text(encoding="utf-8").strip()


def create_session_token(
    session: Session,
    session_manager: SessionManager,
    settings: Settings,
    user_id: int,
    ip_address: str,
    user_agent: str,
) -> str:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=settings.session_max_age_seconds)
    session.execute(delete(AuthSession).where(AuthSession.expires_at <= now))
    token = session_manager.issue_token()
    token_hash = session_manager.fingerprint(token)
    session.add(
        AuthSession(
            user_id=user_id,
            session_token_hash=token_hash,
            ip_address=ip_address[:120],
            user_agent=user_agent,
            created_at=now,
            last_seen_at=now,
            expires_at=expires_at,
        )
    )
    session.commit()
    return token


def revoke_session_token(session: Session, session_manager: SessionManager, token: str) -> None:
    token_hash = session_manager.fingerprint(token)
    session.execute(delete(AuthSession).where(AuthSession.session_token_hash == token_hash))
    session.commit()


def load_identity_from_session_token(
    session: Session,
    session_manager: SessionManager,
    token: str,
    settings: Settings,
) -> UserIdentity:
    now = datetime.now(timezone.utc)
    token_hash = session_manager.fingerprint(token)
    auth_session = session.scalar(
        select(AuthSession)
        .options(selectinload(AuthSession.user))
        .where(AuthSession.session_token_hash == token_hash)
    )
    if auth_session is None:
        raise InvalidSessionError("Session is invalid. Please sign in again.")
    if normalize_datetime(auth_session.expires_at) <= now:
        session.execute(delete(AuthSession).where(AuthSession.id == auth_session.id))
        session.commit()
        raise InvalidSessionError("Session expired. Please sign in again.")
    auth_session.last_seen_at = now
    user = auth_session.user
    if user is None:
        session.execute(delete(AuthSession).where(AuthSession.id == auth_session.id))
        session.commit()
        raise InvalidSessionError("Session is invalid. Please sign in again.")
    session.commit()
    return user_to_identity(user)


def save_push_subscription(session: Session, user_id: int, payload: PushSubscriptionCreate) -> None:
    existing = session.scalar(select(PushSubscription).where(PushSubscription.endpoint == payload.endpoint))
    now = datetime.now(timezone.utc)
    if existing is None:
        session.add(
            PushSubscription(
                user_id=user_id,
                endpoint=payload.endpoint,
                p256dh=payload.keys.p256dh,
                auth=payload.keys.auth,
                created_at=now,
                updated_at=now,
            )
        )
    else:
        existing.user_id = user_id
        existing.p256dh = payload.keys.p256dh
        existing.auth = payload.keys.auth
        existing.updated_at = now
    session.commit()


def delete_push_subscription(session: Session, user_id: int, endpoint: str) -> None:
    session.execute(delete(PushSubscription).where(PushSubscription.user_id == user_id, PushSubscription.endpoint == endpoint))
    session.commit()


def list_push_subscriptions(session: Session, user_id: int) -> list[PushSubscription]:
    return list(session.scalars(select(PushSubscription).where(PushSubscription.user_id == user_id)))


def delete_push_subscriptions(session: Session, user_id: int, endpoints: list[str]) -> None:
    if not endpoints:
        return
    session.execute(
        delete(PushSubscription).where(
            PushSubscription.user_id == user_id,
            PushSubscription.endpoint.in_(endpoints),
        )
    )
    session.commit()


def are_friends(session: Session, user_id: int, friend_id: int) -> bool:
    return session.scalar(
        select(Friendship).where(Friendship.user_id == user_id, Friendship.friend_id == friend_id)
    ) is not None


def pending_request_between(session: Session, user_a: int, user_b: int) -> FriendRequest | None:
    return session.scalar(
        select(FriendRequest).where(
            FriendRequest.status == "pending",
            or_(
                and_(FriendRequest.requester_id == user_a, FriendRequest.addressee_id == user_b),
                and_(FriendRequest.requester_id == user_b, FriendRequest.addressee_id == user_a),
            ),
        )
    )


def create_friend_request(session: Session, current_user_id: int, username: str) -> FriendRequestRead:
    target_username = normalize_username(username)
    target_user = session.scalar(select(User).where(User.username == target_username))
    if target_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    if target_user.id == current_user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot add yourself.")
    if are_friends(session, current_user_id, target_user.id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This user is already your friend.")
    if pending_request_between(session, current_user_id, target_user.id) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A friend request already exists.")

    request_obj = FriendRequest(requester_id=current_user_id, addressee_id=target_user.id, status="pending")
    session.add(request_obj)
    session.commit()
    session.refresh(request_obj)
    session.refresh(target_user)
    request_obj.requester = session.get(User, current_user_id)
    request_obj.addressee = target_user
    return friend_request_to_schema(request_obj)


def respond_to_friend_request(session: Session, request_id: int, current_user_id: int, accept: bool) -> FriendRequestRead:
    request_obj = session.scalar(
        select(FriendRequest)
        .options(selectinload(FriendRequest.requester), selectinload(FriendRequest.addressee))
        .where(FriendRequest.id == request_id)
    )
    if request_obj is None or request_obj.addressee_id != current_user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Friend request not found.")
    if request_obj.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Friend request is already resolved.")

    request_obj.status = "accepted" if accept else "rejected"
    request_obj.responded_at = datetime.now(timezone.utc)

    if accept and not are_friends(session, request_obj.requester_id, request_obj.addressee_id):
        session.add(Friendship(user_id=request_obj.requester_id, friend_id=request_obj.addressee_id))
        session.add(Friendship(user_id=request_obj.addressee_id, friend_id=request_obj.requester_id))

    session.commit()
    session.refresh(request_obj)
    return friend_request_to_schema(request_obj)


def list_friend_requests(session: Session, current_user_id: int) -> list[FriendRequestRead]:
    incoming = session.scalars(
        select(FriendRequest)
        .options(selectinload(FriendRequest.requester), selectinload(FriendRequest.addressee))
        .where(FriendRequest.addressee_id == current_user_id, FriendRequest.status == "pending")
        .order_by(FriendRequest.created_at.desc())
    )
    return [friend_request_to_schema(item) for item in incoming]


def list_friend_identities(session: Session, current_user_id: int) -> list[UserIdentity]:
    friend_alias = aliased(User)
    friends = session.execute(
        select(friend_alias)
        .join(Friendship, Friendship.friend_id == friend_alias.id)
        .where(Friendship.user_id == current_user_id)
    ).scalars()
    return [user_to_identity(friend) for friend in friends]


async def broadcast_to_user(manager: ConnectionManager, user_id: int, payload: dict) -> None:
    await manager.broadcast(updates_channel_key(user_id), payload)


async def broadcast_presence_to_friends(
    database: Database,
    manager: ConnectionManager,
    current_identity: UserIdentity,
    is_online: bool,
) -> None:
    friends = await asyncio.to_thread(
        run_with_session,
        database,
        lambda session: list_friend_identities(session, current_identity.id),
    )
    payload = {
        "type": "presence",
        "user_id": current_identity.id,
        "username": current_identity.username,
        "display_name": current_identity.display_name,
        "avatar_url": avatar_url_for(current_identity),
        "is_online": is_online,
    }
    for friend in friends:
        await broadcast_to_user(manager, friend.id, payload)


async def broadcast_friends_changed_to_contacts(
    database: Database,
    manager: ConnectionManager,
    current_user_id: int,
) -> None:
    friends = await asyncio.to_thread(
        run_with_session,
        database,
        lambda session: list_friend_identities(session, current_user_id),
    )
    for friend in friends:
        await broadcast_to_user(manager, friend.id, {"type": "friends-changed"})


def search_users(session: Session, current_user_id: int, query: str, manager: ConnectionManager) -> list[UserSearchRead]:
    normalized = normalize_username(query)
    users = session.scalars(
        select(User).where(User.id != current_user_id, User.username.contains(normalized)).order_by(User.username.asc()).limit(10)
    )
    results: list[UserSearchRead] = []
    for user in users:
        friendship = are_friends(session, current_user_id, user.id)
        request_state = "none"
        pending = pending_request_between(session, current_user_id, user.id)
        if pending is not None:
            request_state = "incoming" if pending.addressee_id == current_user_id else "outgoing"
        results.append(
            UserSearchRead(
                username=user.username,
                display_name=user.display_name,
                initials=initials_for_name(user.display_name),
                avatar_url=avatar_url_for(user),
                is_friend=friendship,
                request_state=request_state,
                is_online=manager.is_online(user.id),
            )
        )
    return results


def list_friends(session: Session, current_user_id: int, manager: ConnectionManager) -> list[FriendRead]:
    friend_alias = aliased(User)
    friendships = session.execute(
        select(Friendship, friend_alias)
        .join(friend_alias, Friendship.friend_id == friend_alias.id)
        .where(Friendship.user_id == current_user_id)
        .order_by(friend_alias.display_name.asc())
    ).all()

    items: list[FriendRead] = []
    for friendship, friend in friendships:
        last_message = session.scalar(
            select(DirectMessage)
            .options(selectinload(DirectMessage.sender), selectinload(DirectMessage.recipient))
            .where(
                or_(
                    and_(DirectMessage.sender_id == current_user_id, DirectMessage.recipient_id == friend.id),
                    and_(DirectMessage.sender_id == friend.id, DirectMessage.recipient_id == current_user_id),
                )
            )
            .order_by(DirectMessage.sent_at.desc())
            .limit(1)
        )
        items.append(
            FriendRead(
                username=friend.username,
                display_name=friend.display_name,
                initials=initials_for_name(friend.display_name),
                avatar_url=avatar_url_for(friend),
                is_online=manager.is_online(friend.id),
                last_message=last_message.content if last_message else None,
                last_message_at=last_message.sent_at if last_message else None,
                unread_count=0,
            )
        )
    return items


def ensure_friend_or_404(session: Session, current_user_id: int, target_username: str) -> User:
    normalized = normalize_username(target_username)
    target_user = session.scalar(select(User).where(User.username == normalized))
    if target_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    if not are_friends(session, current_user_id, target_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can chat only with accepted friends.")
    return target_user


def get_direct_messages(session: Session, current_user_id: int, target_username: str, limit: int) -> list[DirectMessageRead]:
    target_user = ensure_friend_or_404(session, current_user_id, target_username)
    messages = session.scalars(
        select(DirectMessage)
        .options(selectinload(DirectMessage.sender), selectinload(DirectMessage.recipient))
        .where(
            or_(
                and_(DirectMessage.sender_id == current_user_id, DirectMessage.recipient_id == target_user.id),
                and_(DirectMessage.sender_id == target_user.id, DirectMessage.recipient_id == current_user_id),
            )
        )
        .order_by(DirectMessage.sent_at.desc())
        .limit(limit)
    ).all()
    items = list(messages)
    items.reverse()
    return [direct_message_to_schema(message) for message in items]


def create_direct_message(session: Session, current_user_id: int, target_username: str, content: str) -> DirectMessageRead:
    target_user = ensure_friend_or_404(session, current_user_id, target_username)
    sender = session.get(User, current_user_id)
    if sender is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User session is invalid.")

    message = DirectMessage(sender_id=current_user_id, recipient_id=target_user.id, content=content)
    session.add(message)
    session.commit()
    session.refresh(message)
    message.sender = sender
    message.recipient = target_user
    return direct_message_to_schema(message)


def create_direct_file_message(
    session: Session,
    current_user_id: int,
    target_username: str,
    file_name: str,
    stored_path: str,
    mime_type: str,
    file_size: int,
) -> DirectMessageRead:
    target_user = ensure_friend_or_404(session, current_user_id, target_username)
    sender = session.get(User, current_user_id)
    if sender is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User session is invalid.")

    message = DirectMessage(
        sender_id=current_user_id,
        recipient_id=target_user.id,
        content="",
        attachment_name=file_name,
        attachment_path=stored_path,
        attachment_size=file_size,
        attachment_mime_type=mime_type,
        attachment_token=secrets.token_urlsafe(24),
    )
    session.add(message)
    session.commit()
    session.refresh(message)
    message.sender = sender
    message.recipient = target_user
    return direct_message_to_schema(message)


def validate_upload(file: UploadFile, file_size: int) -> None:
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File name is missing.")
    if file_size <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")
    if file_size > MAX_ATTACHMENT_SIZE:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File is too large.")
    mime_type = (file.content_type or "application/octet-stream").lower()
    if mime_type not in INLINE_ATTACHMENT_TYPES and not mime_type.startswith("text/"):
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="File type is not allowed.")


def validate_avatar_upload(file: UploadFile, file_size: int) -> str:
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Avatar file name is missing.")
    if file_size <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Avatar file is empty.")
    if file_size > MAX_AVATAR_SIZE:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Avatar is too large.")
    mime_type = (file.content_type or "application/octet-stream").lower()
    if mime_type not in ALLOWED_AVATAR_TYPES:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Avatar type is not allowed.")
    return mime_type


def save_or_update_avatar(
    session: Session,
    current_user_id: int,
    file_name: str,
    stored_path: str,
    mime_type: str,
) -> UserRead:
    user = session.get(User, current_user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User session is invalid.")

    old_path = user.avatar_path
    user.avatar_name = file_name
    user.avatar_path = stored_path
    user.avatar_mime_type = mime_type
    user.avatar_token = user.avatar_token or secrets.token_urlsafe(24)
    session.commit()
    session.refresh(user)

    if old_path and old_path != stored_path:
        Path(old_path).unlink(missing_ok=True)

    return user_to_schema(user)


def build_push_payload(message: DirectMessageRead, friend_username: str) -> str:
    body = message.content or message.attachment_name or "New attachment"
    return json.dumps(
        {
            "title": message.sender_display_name or message.sender_username,
            "body": body,
            "tag": f"dm:{message.sender_username}:{friend_username}",
            "data": {
                "friend_username": message.sender_username,
                "url": f"/?chat={message.sender_username}",
            },
        }
    )


def send_push_notifications(
    database: Database,
    settings: Settings,
    recipient_user_id: int,
    payload: str,
) -> None:
    if not push_enabled(settings):
        return

    subscriptions = run_with_session(database, lambda session: list_push_subscriptions(session, recipient_user_id))
    if not subscriptions:
        return

    expired_endpoints: list[str] = []
    for subscription in subscriptions:
        subscription_info = {
            "endpoint": subscription.endpoint,
            "keys": {
                "p256dh": subscription.p256dh,
                "auth": subscription.auth,
            },
        }
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=settings.push_private_key,
                vapid_claims=vapid_claims(settings),
            )
        except WebPushException as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in {404, 410}:
                expired_endpoints.append(subscription.endpoint)
        except Exception:
            continue

    if expired_endpoints:
        run_with_session(
            database,
            lambda session: delete_push_subscriptions(session, recipient_user_id, expired_endpoints),
        )


def ensure_sqlite_schema_compatibility(database: Database) -> None:
    if database.engine.url.get_backend_name() != "sqlite":
        return

    database_path = database.engine.url.database
    if not database_path:
        return

    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        return

    inspector = sa_inspect(database.engine)
    required_tables = {"users", "friend_requests", "friendships", "direct_messages"}
    existing_tables = set(inspector.get_table_names())
    direct_message_columns = {column["name"] for column in inspector.get_columns("direct_messages")} if "direct_messages" in existing_tables else set()
    user_columns = {column["name"] for column in inspector.get_columns("users")} if "users" in existing_tables else set()
    required_columns = {"attachment_name", "attachment_path", "attachment_size", "attachment_mime_type", "attachment_token"}
    required_user_columns = {"avatar_name", "avatar_path", "avatar_mime_type", "avatar_token"}

    if not required_tables.issubset(existing_tables):
        return

    alter_statements: list[str] = []
    if "attachment_name" not in direct_message_columns:
        alter_statements.append("ALTER TABLE direct_messages ADD COLUMN attachment_name VARCHAR(255)")
    if "attachment_path" not in direct_message_columns:
        alter_statements.append("ALTER TABLE direct_messages ADD COLUMN attachment_path VARCHAR(255)")
    if "attachment_size" not in direct_message_columns:
        alter_statements.append("ALTER TABLE direct_messages ADD COLUMN attachment_size INTEGER")
    if "attachment_mime_type" not in direct_message_columns:
        alter_statements.append("ALTER TABLE direct_messages ADD COLUMN attachment_mime_type VARCHAR(120)")
    if "attachment_token" not in direct_message_columns:
        alter_statements.append("ALTER TABLE direct_messages ADD COLUMN attachment_token VARCHAR(64)")
    if "avatar_name" not in user_columns:
        alter_statements.append("ALTER TABLE users ADD COLUMN avatar_name VARCHAR(255)")
    if "avatar_path" not in user_columns:
        alter_statements.append("ALTER TABLE users ADD COLUMN avatar_path VARCHAR(255)")
    if "avatar_mime_type" not in user_columns:
        alter_statements.append("ALTER TABLE users ADD COLUMN avatar_mime_type VARCHAR(120)")
    if "avatar_token" not in user_columns:
        alter_statements.append("ALTER TABLE users ADD COLUMN avatar_token VARCHAR(64)")

    if not alter_statements and required_columns.issubset(direct_message_columns) and required_user_columns.issubset(user_columns):
        return

    with database.engine.begin() as connection:
        for statement in alter_statements:
            connection.execute(text(statement))
        if "attachment_token" not in direct_message_columns:
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_direct_messages_attachment_token ON direct_messages (attachment_token)"))
        if "avatar_token" not in user_columns:
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_avatar_token ON users (avatar_token)"))


def issue_session_cookie(response: Response, settings: Settings, token: str) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.session_max_age_seconds)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.session_max_age_seconds,
        expires=expires_at,
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def current_identity_from_request(request: Request, database: Database, session_manager: SessionManager, settings: Settings) -> UserIdentity:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    try:
        identity = run_with_session(
            database,
            lambda session: load_identity_from_session_token(session, session_manager, token, settings),
        )
    except InvalidSessionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return identity


def current_identity_from_websocket(
    websocket: WebSocket, database: Database, session_manager: SessionManager, settings: Settings
) -> UserIdentity:
    token = websocket.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    try:
        identity = run_with_session(
            database,
            lambda session: load_identity_from_session_token(session, session_manager, token, settings),
        )
    except InvalidSessionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return identity


def create_app(settings: Settings | None = None, database_url: str | None = None) -> FastAPI:
    resolved_settings = settings or load_settings(database_url)
    ensure_push_configuration(resolved_settings)
    resolved_settings.validate()
    database = Database(resolved_settings.database_url)
    session_manager = SessionManager(resolved_settings.secret_key)
    manager = ConnectionManager()
    updates_manager = ConnectionManager()
    static_dir = Path(__file__).resolve().parent / "static"

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        ensure_sqlite_schema_compatibility(database)
        database.init_db()
        yield

    app = FastAPI(
        title=resolved_settings.app_name,
        summary="Private messaging app with friends, requests, and direct chat.",
        version="2.0.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings

    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
    app.add_middleware(
        SecurityHeadersMiddleware,
        enable_hsts=resolved_settings.cookie_secure or resolved_settings.force_https,
        hsts_max_age=resolved_settings.hsts_max_age_seconds,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=resolved_settings.allowed_hosts)


    if resolved_settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=resolved_settings.allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type"],
        )

    if resolved_settings.force_https:
        app.add_middleware(HTTPSRedirectMiddleware)

    app.mount("/assets", StaticFiles(directory=static_dir), name="assets")

    @app.get("/", include_in_schema=False)
    def frontend() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/service-worker.js", include_in_schema=False)
    def service_worker() -> FileResponse:
        return FileResponse(static_dir / "service-worker.js", media_type="application/javascript")

    @app.get("/api/health", response_model=HealthRead)
    def healthcheck() -> HealthRead:
        return HealthRead(status="ok", database_backend=detect_database_backend(resolved_settings.database_url))

    @app.get("/api/session", response_model=SessionRead)
    def session_info(request: Request, response: Response) -> SessionRead:
        response.headers["Cache-Control"] = "no-store"
        try:
            identity = current_identity_from_request(request, database, session_manager, resolved_settings)
        except HTTPException:
            clear_session_cookie(response, resolved_settings)
            return SessionRead(
                authenticated=False,
                user=None,
                app_name=resolved_settings.app_name,
                push_supported=push_enabled(resolved_settings),
                push_public_key=resolved_settings.push_public_key or None,
            )
        return SessionRead(
            authenticated=True,
            user=identity_to_user_schema(identity),
            app_name=resolved_settings.app_name,
            push_supported=push_enabled(resolved_settings),
            push_public_key=resolved_settings.push_public_key or None,
        )

    @app.post("/api/auth/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
    def register(payload: AuthRequest, request: Request, response: Response) -> UserRead:
        response.headers["Cache-Control"] = "no-store"
        client_key = extract_client_key(request, payload.username)
        try:
            rate_limiter.hit(
                key=f"register:{client_key}",
                limit=resolved_settings.auth_rate_limit_count,
                window_seconds=resolved_settings.auth_rate_limit_window_seconds,
            )
        except RateLimitError as exc:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
        identity = run_with_session(database, lambda session: create_user(session, payload))
        ip_address, user_agent = extract_ip_and_user_agent(request)
        session_token = run_with_session(
            database,
            lambda session: create_session_token(
                session,
                session_manager,
                resolved_settings,
                identity.id,
                ip_address,
                user_agent,
            ),
        )
        issue_session_cookie(response, resolved_settings, session_token)
        return identity_to_user_schema(identity)

    @app.post("/api/auth/login", response_model=UserRead)
    def login(payload: AuthRequest, request: Request, response: Response) -> UserRead:
        response.headers["Cache-Control"] = "no-store"
        client_key = extract_client_key(request, payload.username)
        try:
            rate_limiter.hit(
                key=f"login:{client_key}",
                limit=resolved_settings.auth_rate_limit_count,
                window_seconds=resolved_settings.auth_rate_limit_window_seconds,
            )
        except RateLimitError as exc:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
        identity = run_with_session(database, lambda session: authenticate_user(session, payload))
        ip_address, user_agent = extract_ip_and_user_agent(request)
        session_token = run_with_session(
            database,
            lambda session: create_session_token(
                session,
                session_manager,
                resolved_settings,
                identity.id,
                ip_address,
                user_agent,
            ),
        )
        issue_session_cookie(response, resolved_settings, session_token)
        return identity_to_user_schema(identity)

    @app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout(request: Request, response: Response) -> Response:
        token = request.cookies.get(resolved_settings.session_cookie_name)
        if token:
            run_with_session(database, lambda session: revoke_session_token(session, session_manager, token))
        clear_session_cookie(response, resolved_settings)
        return response

    @app.patch("/api/users/me", response_model=UserRead)
    async def update_current_user(payload: ProfileUpdateRequest, request: Request, response: Response) -> UserRead:
        response.headers["Cache-Control"] = "no-store"
        identity = current_identity_from_request(request, database, session_manager, resolved_settings)
        updated_user = run_with_session(database, lambda session: update_profile(session, identity.id, payload))
        refreshed_identity = run_with_session(database, lambda session: load_identity(session, identity.id))
        if refreshed_identity is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User was not found.")
        asyncio.create_task(broadcast_friends_changed_to_contacts(database, updates_manager, identity.id))
        return updated_user

    @app.post("/api/users/me/avatar", response_model=UserRead)
    async def upload_avatar(request: Request, file: UploadFile = File(...)) -> UserRead:
        identity = current_identity_from_request(request, database, session_manager, resolved_settings)
        avatars_dir = Path(resolved_settings.storage_dir) / "uploads" / "avatars"
        avatars_dir.mkdir(parents=True, exist_ok=True)

        safe_name = Path(file.filename or "avatar.bin").name
        suffix = Path(safe_name).suffix or ".bin"
        stored_name = f"{identity.id}-{secrets.token_hex(12)}{suffix}"
        stored_path = avatars_dir / stored_name

        size = 0
        with stored_path.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_AVATAR_SIZE:
                    destination.close()
                    stored_path.unlink(missing_ok=True)
                    raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Avatar is too large.")
                destination.write(chunk)

        mime_type = validate_avatar_upload(file, size)
        updated_user = run_with_session(
            database,
            lambda session: save_or_update_avatar(session, identity.id, safe_name, str(stored_path), mime_type),
        )
        asyncio.create_task(broadcast_friends_changed_to_contacts(database, updates_manager, identity.id))
        return updated_user

    @app.get("/api/avatars/{avatar_token}")
    def download_avatar(avatar_token: str, request: Request) -> FileResponse:
        current_identity_from_request(request, database, session_manager, resolved_settings)

        def load_avatar(session: Session) -> User:
            user = session.scalar(select(User).where(User.avatar_token == avatar_token))
            if user is None or not user.avatar_path:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Avatar not found.")
            return user

        user = run_with_session(database, load_avatar)
        file_path = Path(user.avatar_path)
        if not file_path.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Avatar content is missing.")
        return FileResponse(file_path, filename=user.avatar_name or file_path.name, media_type=user.avatar_mime_type)

    @app.get("/api/friends", response_model=list[FriendRead])
    def read_friends(request: Request) -> list[FriendRead]:
        identity = current_identity_from_request(request, database, session_manager, resolved_settings)
        return run_with_session(database, lambda session: list_friends(session, identity.id, updates_manager))

    @app.get("/api/friend-requests", response_model=list[FriendRequestRead])
    def read_friend_requests(request: Request) -> list[FriendRequestRead]:
        identity = current_identity_from_request(request, database, session_manager, resolved_settings)
        return run_with_session(database, lambda session: list_friend_requests(session, identity.id))

    @app.post("/api/push/subscribe", status_code=status.HTTP_204_NO_CONTENT)
    def subscribe_push(payload: PushSubscriptionCreate, request: Request) -> Response:
        identity = current_identity_from_request(request, database, session_manager, resolved_settings)
        if not push_enabled(resolved_settings):
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Push notifications are not configured.")
        run_with_session(database, lambda session: save_push_subscription(session, identity.id, payload))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/api/push/unsubscribe", status_code=status.HTTP_204_NO_CONTENT)
    def unsubscribe_push(payload: PushSubscriptionCreate, request: Request) -> Response:
        identity = current_identity_from_request(request, database, session_manager, resolved_settings)
        run_with_session(database, lambda session: delete_push_subscription(session, identity.id, payload.endpoint))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/users/search", response_model=list[UserSearchRead])
    def search_people(query: str, request: Request) -> list[UserSearchRead]:
        identity = current_identity_from_request(request, database, session_manager, resolved_settings)
        if not query.strip():
            return []
        return run_with_session(database, lambda session: search_users(session, identity.id, query, updates_manager))

    @app.post("/api/friend-requests", response_model=FriendRequestRead, status_code=status.HTTP_201_CREATED)
    async def send_friend_request(payload: FriendRequestCreate, request: Request) -> FriendRequestRead:
        identity = current_identity_from_request(request, database, session_manager, resolved_settings)
        friend_request = run_with_session(database, lambda session: create_friend_request(session, identity.id, payload.username))
        asyncio.create_task(broadcast_to_user(updates_manager, friend_request.addressee.id, {"type": "requests-changed"}))
        return friend_request

    @app.post("/api/friend-requests/{request_id}/accept", response_model=FriendRequestRead)
    async def accept_friend_request(request_id: int, request: Request) -> FriendRequestRead:
        identity = current_identity_from_request(request, database, session_manager, resolved_settings)
        friend_request = run_with_session(database, lambda session: respond_to_friend_request(session, request_id, identity.id, True))
        asyncio.create_task(broadcast_to_user(updates_manager, friend_request.requester.id, {"type": "friends-changed"}))
        asyncio.create_task(broadcast_to_user(updates_manager, friend_request.addressee.id, {"type": "friends-changed"}))
        asyncio.create_task(broadcast_to_user(updates_manager, friend_request.requester.id, {"type": "requests-changed"}))
        asyncio.create_task(broadcast_to_user(updates_manager, friend_request.addressee.id, {"type": "requests-changed"}))
        return friend_request

    @app.post("/api/friend-requests/{request_id}/reject", response_model=FriendRequestRead)
    async def reject_friend_request(request_id: int, request: Request) -> FriendRequestRead:
        identity = current_identity_from_request(request, database, session_manager, resolved_settings)
        friend_request = run_with_session(database, lambda session: respond_to_friend_request(session, request_id, identity.id, False))
        asyncio.create_task(broadcast_to_user(updates_manager, friend_request.requester.id, {"type": "requests-changed"}))
        asyncio.create_task(broadcast_to_user(updates_manager, friend_request.addressee.id, {"type": "requests-changed"}))
        return friend_request

    @app.get("/api/direct/{friend_username}/messages", response_model=list[DirectMessageRead])
    def read_direct_messages(friend_username: str, request: Request) -> list[DirectMessageRead]:
        identity = current_identity_from_request(request, database, session_manager, resolved_settings)
        return run_with_session(
            database,
            lambda session: get_direct_messages(session, identity.id, friend_username, resolved_settings.message_history_limit),
        )

    @app.post("/api/direct/{friend_username}/files", response_model=DirectMessageRead, status_code=status.HTTP_201_CREATED)
    async def upload_direct_file(friend_username: str, request: Request, file: UploadFile = File(...)) -> DirectMessageRead:
        identity = current_identity_from_request(request, database, session_manager, resolved_settings)
        uploads_dir = Path(resolved_settings.storage_dir) / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)

        safe_name = Path(file.filename or "attachment.bin").name
        suffix = Path(safe_name).suffix
        stored_name = f"{secrets.token_hex(16)}{suffix}"
        stored_path = uploads_dir / stored_name

        size = 0
        with stored_path.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_ATTACHMENT_SIZE:
                    destination.close()
                    stored_path.unlink(missing_ok=True)
                    raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File is too large.")
                destination.write(chunk)

        validate_upload(file, size)
        saved_message = run_with_session(
            database,
            lambda session: create_direct_file_message(
                session,
                identity.id,
                friend_username,
                safe_name,
                str(stored_path),
                (file.content_type or "application/octet-stream").lower(),
                size,
            ),
        )
        target_identity = run_with_session(
            database, lambda session: user_to_identity(ensure_friend_or_404(session, identity.id, friend_username))
        )
        await manager.broadcast(
            direct_channel_key(identity.id, target_identity.id),
            {"type": "message", "message": saved_message.model_dump(mode="json")},
        )
        if target_identity.id != identity.id:
            asyncio.create_task(
                asyncio.to_thread(
                    send_push_notifications,
                    database,
                    resolved_settings,
                    target_identity.id,
                    build_push_payload(saved_message, friend_username),
                )
            )
        return saved_message

    @app.get("/api/files/{attachment_token}")
    def download_attachment(attachment_token: str, request: Request) -> FileResponse:
        identity = current_identity_from_request(request, database, session_manager, resolved_settings)

        def load_attachment(session: Session) -> DirectMessage:
            message = session.scalar(
                select(DirectMessage)
                .options(selectinload(DirectMessage.sender), selectinload(DirectMessage.recipient))
                .where(DirectMessage.attachment_token == attachment_token)
            )
            if message is None or not message.attachment_path:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found.")
            participant_ids = {message.sender_id, message.recipient_id}
            if identity.id not in participant_ids:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this file.")
            return message

        message = run_with_session(database, load_attachment)
        file_path = Path(message.attachment_path)
        if not file_path.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File content is missing.")
        return FileResponse(file_path, filename=message.attachment_name or file_path.name, media_type=message.attachment_mime_type)

    @app.websocket("/ws/updates")
    async def updates_socket(websocket: WebSocket) -> None:
        try:
            identity = current_identity_from_websocket(websocket, database, session_manager, resolved_settings)
        except HTTPException as exc:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=exc.detail)
            return

        if resolved_settings.allowed_origins:
            origin = websocket.headers.get("origin")
            if origin and origin not in resolved_settings.allowed_origins:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Origin is not allowed.")
                return

        channel = updates_channel_key(identity.id)
        became_online = await updates_manager.mark_online(identity.id)
        await updates_manager.connect(channel, websocket)
        if became_online:
            asyncio.create_task(broadcast_presence_to_friends(database, updates_manager, identity, True))

        try:
            await websocket.send_json({"type": "ready"})
            while True:
                raw_payload = await websocket.receive_text()
                if raw_payload.strip().lower() == "ping":
                    await websocket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            pass
        finally:
            await updates_manager.disconnect(channel, websocket)
            became_offline = await updates_manager.mark_offline(identity.id)
            if became_offline:
                asyncio.create_task(broadcast_presence_to_friends(database, updates_manager, identity, False))

    @app.websocket("/ws/direct/{friend_username}")
    async def direct_socket(websocket: WebSocket, friend_username: str) -> None:
        try:
            identity = current_identity_from_websocket(websocket, database, session_manager, resolved_settings)
        except HTTPException as exc:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=exc.detail)
            return

        if resolved_settings.allowed_origins:
            origin = websocket.headers.get("origin")
            if origin and origin not in resolved_settings.allowed_origins:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Origin is not allowed.")
                return

        try:
            history = await asyncio.to_thread(
                run_with_session,
                database,
                lambda session: get_direct_messages(session, identity.id, friend_username, resolved_settings.message_history_limit),
            )
            target_identity = await asyncio.to_thread(
                run_with_session,
                database,
                lambda session: user_to_identity(ensure_friend_or_404(session, identity.id, friend_username)),
            )
        except HTTPException as exc:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=exc.detail)
            return

        channel = direct_channel_key(identity.id, target_identity.id)
        await manager.connect(channel, websocket)
        try:
            await websocket.send_json(
                {
                    "type": "history",
                    "friend": {
                        "username": target_identity.username,
                        "display_name": target_identity.display_name,
                        "initials": initials_for_name(target_identity.display_name),
                        "avatar_url": avatar_url_for(target_identity),
                        "is_online": updates_manager.is_online(target_identity.id),
                    },
                    "messages": [item.model_dump(mode="json") for item in history],
                }
            )
            while True:
                raw_payload = await websocket.receive_text()
                try:
                    payload = json.loads(raw_payload)
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "detail": "Payload must be valid JSON."})
                    continue

                message_type = str(payload.get("type", "message")).strip()
                if message_type in {"call-offer", "call-answer", "ice-candidate", "call-end", "call-decline"}:
                    signal_payload = {
                        "type": message_type,
                        "sender_username": identity.username,
                        "sender_display_name": identity.display_name,
                    }
                    if "sdp" in payload:
                        signal_payload["sdp"] = payload["sdp"]
                    if "candidate" in payload:
                        signal_payload["candidate"] = payload["candidate"]
                    await manager.broadcast(channel, signal_payload)
                    continue

                content = str(payload.get("content", "")).strip()
                if not content:
                    await websocket.send_json({"type": "error", "detail": "Message cannot be empty."})
                    continue
                if len(content) > 2000:
                    await websocket.send_json({"type": "error", "detail": "Message is too long."})
                    continue

                try:
                    rate_limiter.hit(
                        key=f"message:{identity.id}",
                        limit=resolved_settings.message_rate_limit_count,
                        window_seconds=resolved_settings.message_rate_limit_window_seconds,
                    )
                except RateLimitError as exc:
                    await websocket.send_json({"type": "error", "detail": str(exc)})
                    continue

                saved_message = await asyncio.to_thread(
                    run_with_session,
                    database,
                    lambda session: create_direct_message(session, identity.id, friend_username, content),
                )
                await manager.broadcast(channel, {"type": "message", "message": saved_message.model_dump(mode="json")})
                if target_identity.id != identity.id:
                    asyncio.create_task(
                        asyncio.to_thread(
                            send_push_notifications,
                            database,
                            resolved_settings,
                            target_identity.id,
                            build_push_payload(saved_message, friend_username),
                        )
                    )
        except WebSocketDisconnect:
            pass
        finally:
            await manager.disconnect(channel, websocket)

    return app
