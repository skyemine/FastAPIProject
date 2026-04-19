from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, aliased, selectinload
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .chat import ConnectionManager
from .config import Settings, load_settings
from .database import Database
from .middleware import SecurityHeadersMiddleware
from .models import DirectMessage, FriendRequest, Friendship, User
from .schemas import (
    AuthRequest,
    DirectMessageRead,
    FriendRead,
    FriendRequestCreate,
    FriendRequestRead,
    HealthRead,
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

T = TypeVar("T")
USERNAME_PATTERN = re.compile(r"[^a-z0-9_]+")


@dataclass(slots=True)
class UserIdentity:
    id: int
    username: str
    display_name: str
    created_at: str


def direct_channel_key(user_a: int, user_b: int) -> str:
    ordered = sorted((user_a, user_b))
    return f"dm:{ordered[0]}:{ordered[1]}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_username(value: str) -> str:
    normalized = USERNAME_PATTERN.sub("", value.strip().lower())
    if len(normalized) < 3:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
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
        created_at=user.created_at.isoformat(),
    )


def identity_to_user_schema(identity: UserIdentity) -> UserRead:
    return UserRead(
        id=identity.id,
        username=identity.username,
        display_name=identity.display_name,
        initials=initials_for_name(identity.display_name),
        created_at=parse_isoformat(identity.created_at),
    )


def user_to_schema(user: User) -> UserRead:
    return UserRead(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        initials=initials_for_name(user.display_name),
        created_at=user.created_at,
    )


def friend_request_to_schema(request_obj: FriendRequest) -> FriendRequestRead:
    return FriendRequestRead(
        id=request_obj.id,
        requester=user_to_schema(request_obj.requester),
        addressee=user_to_schema(request_obj.addressee),
        status=request_obj.status,
        created_at=request_obj.created_at,
        responded_at=request_obj.responded_at,
    )


def direct_message_to_schema(message: DirectMessage) -> DirectMessageRead:
    return DirectMessageRead(
        id=message.id,
        sender_username=message.sender.username,
        sender_display_name=message.sender.display_name,
        content=message.content,
        sent_at=message.sent_at,
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
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
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


def issue_session_cookie(response: Response, session_manager: SessionManager, settings: Settings, user: UserIdentity) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_manager.issue_token(user.id),
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
        max_age=settings.session_max_age_seconds,
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(key=settings.session_cookie_name, path="/")


def current_identity_from_request(request: Request, database: Database, session_manager: SessionManager, settings: Settings) -> UserIdentity:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    try:
        user_id = session_manager.read_token(token, settings.session_max_age_seconds)
    except InvalidSessionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    identity = run_with_session(database, lambda session: load_identity(session, user_id))
    if identity is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User was not found.")
    return identity


def current_identity_from_websocket(
    websocket: WebSocket, database: Database, session_manager: SessionManager, settings: Settings
) -> UserIdentity:
    token = websocket.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    try:
        user_id = session_manager.read_token(token, settings.session_max_age_seconds)
    except InvalidSessionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    identity = run_with_session(database, lambda session: load_identity(session, user_id))
    if identity is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User was not found.")
    return identity


def create_app(settings: Settings | None = None, database_url: str | None = None) -> FastAPI:
    resolved_settings = settings or load_settings(database_url)
    resolved_settings.validate()
    database = Database(resolved_settings.database_url)
    session_manager = SessionManager(resolved_settings.secret_key)
    manager = ConnectionManager()
    static_dir = Path(__file__).resolve().parent / "static"

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.init_db()
        yield

    app = FastAPI(
        title=resolved_settings.app_name,
        summary="Private messaging app with friends, requests, and direct chat.",
        version="2.0.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings

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

    @app.get("/api/health", response_model=HealthRead)
    def healthcheck() -> HealthRead:
        return HealthRead(status="ok", database_backend=detect_database_backend(resolved_settings.database_url))

    @app.get("/api/session", response_model=SessionRead)
    def session_info(request: Request) -> SessionRead:
        try:
            identity = current_identity_from_request(request, database, session_manager, resolved_settings)
        except HTTPException:
            return SessionRead(authenticated=False, user=None, app_name=resolved_settings.app_name)
        return SessionRead(authenticated=True, user=identity_to_user_schema(identity), app_name=resolved_settings.app_name)

    @app.post("/api/auth/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
    def register(payload: AuthRequest, request: Request, response: Response) -> UserRead:
        ip_address = request.client.host if request.client else "unknown"
        try:
            rate_limiter.hit(
                key=f"register:{ip_address}",
                limit=resolved_settings.auth_rate_limit_count,
                window_seconds=resolved_settings.auth_rate_limit_window_seconds,
            )
        except RateLimitError as exc:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
        identity = run_with_session(database, lambda session: create_user(session, payload))
        issue_session_cookie(response, session_manager, resolved_settings, identity)
        return identity_to_user_schema(identity)

    @app.post("/api/auth/login", response_model=UserRead)
    def login(payload: AuthRequest, request: Request, response: Response) -> UserRead:
        ip_address = request.client.host if request.client else "unknown"
        try:
            rate_limiter.hit(
                key=f"login:{ip_address}",
                limit=resolved_settings.auth_rate_limit_count,
                window_seconds=resolved_settings.auth_rate_limit_window_seconds,
            )
        except RateLimitError as exc:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
        identity = run_with_session(database, lambda session: authenticate_user(session, payload))
        issue_session_cookie(response, session_manager, resolved_settings, identity)
        return identity_to_user_schema(identity)

    @app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout(response: Response) -> Response:
        clear_session_cookie(response, resolved_settings)
        return response

    @app.get("/api/friends", response_model=list[FriendRead])
    def read_friends(request: Request) -> list[FriendRead]:
        identity = current_identity_from_request(request, database, session_manager, resolved_settings)
        return run_with_session(database, lambda session: list_friends(session, identity.id, manager))

    @app.get("/api/friend-requests", response_model=list[FriendRequestRead])
    def read_friend_requests(request: Request) -> list[FriendRequestRead]:
        identity = current_identity_from_request(request, database, session_manager, resolved_settings)
        return run_with_session(database, lambda session: list_friend_requests(session, identity.id))

    @app.get("/api/users/search", response_model=list[UserSearchRead])
    def search_people(query: str, request: Request) -> list[UserSearchRead]:
        identity = current_identity_from_request(request, database, session_manager, resolved_settings)
        if not query.strip():
            return []
        return run_with_session(database, lambda session: search_users(session, identity.id, query, manager))

    @app.post("/api/friend-requests", response_model=FriendRequestRead, status_code=status.HTTP_201_CREATED)
    def send_friend_request(payload: FriendRequestCreate, request: Request) -> FriendRequestRead:
        identity = current_identity_from_request(request, database, session_manager, resolved_settings)
        return run_with_session(database, lambda session: create_friend_request(session, identity.id, payload.username))

    @app.post("/api/friend-requests/{request_id}/accept", response_model=FriendRequestRead)
    def accept_friend_request(request_id: int, request: Request) -> FriendRequestRead:
        identity = current_identity_from_request(request, database, session_manager, resolved_settings)
        return run_with_session(database, lambda session: respond_to_friend_request(session, request_id, identity.id, True))

    @app.post("/api/friend-requests/{request_id}/reject", response_model=FriendRequestRead)
    def reject_friend_request(request_id: int, request: Request) -> FriendRequestRead:
        identity = current_identity_from_request(request, database, session_manager, resolved_settings)
        return run_with_session(database, lambda session: respond_to_friend_request(session, request_id, identity.id, False))

    @app.get("/api/direct/{friend_username}/messages", response_model=list[DirectMessageRead])
    def read_direct_messages(friend_username: str, request: Request) -> list[DirectMessageRead]:
        identity = current_identity_from_request(request, database, session_manager, resolved_settings)
        return run_with_session(
            database,
            lambda session: get_direct_messages(session, identity.id, friend_username, resolved_settings.message_history_limit),
        )

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
        await manager.mark_online(identity.id)
        await manager.connect(channel, websocket)
        try:
            await websocket.send_json(
                {
                    "type": "history",
                    "friend": {
                        "username": target_identity.username,
                        "display_name": target_identity.display_name,
                        "initials": initials_for_name(target_identity.display_name),
                        "is_online": manager.is_online(target_identity.id),
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
        except WebSocketDisconnect:
            await manager.disconnect(channel, websocket)
            await manager.mark_offline(identity.id)

    return app
