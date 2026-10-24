"""Вход и доступ (§10): bcrypt, JWT (HS256, 12 ч), лимит попыток входа, изоляция бизнесов.

Любой `/businesses/{bid}/…` идёт через `require_business_owner`: чужой или несуществующий бизнес — 404,
чтобы не подсказывать, какие коды существуют.
"""
from __future__ import annotations

import datetime as dt
import threading
import time
from collections import defaultdict, deque
from typing import Annotated

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import AppSettings
from app.db.models import Business, User
from app.db.session import DbSession

_bearer = HTTPBearer(auto_error=False)
_BCRYPT_MAX_BYTES = 72                      # bcrypt учитывает только первые 72 байта пароля
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:_BCRYPT_MAX_BYTES], bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:_BCRYPT_MAX_BYTES], password_hash.encode("ascii"))
    except ValueError:                      # повреждённый хэш не должен ронять вход в 500
        return False


def create_access_token(user_id: int, settings: AppSettings) -> str:
    now = dt.datetime.now(dt.UTC)
    payload = {"sub": str(user_id), "iat": now, "exp": now + dt.timedelta(hours=settings.jwt_ttl_hours)}
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_user_id(token: str, settings: AppSettings) -> int | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM], options={"require": ["exp", "sub"]})
        return int(payload["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        return None


def _unauthorized(detail: str = "Нужно войти") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


def get_current_user(request: Request, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
                     db: DbSession) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise _unauthorized()
    user_id = decode_user_id(creds.credentials, request.app.state.settings)
    user = db.get(User, user_id) if user_id is not None else None
    if user is None:
        raise _unauthorized("Сессия истекла — войдите снова")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_business_owner(bid: str, user: CurrentUser, db: DbSession) -> Business:
    business = db.get(Business, bid)
    if business is None or business.owner_user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Бизнес не найден")
    return business


OwnedBusiness = Annotated[Business, Depends(require_business_owner)]


class LoginRateLimiter:
    """Не больше N попыток входа в минуту с одного адреса (скользящее окно, в памяти одного процесса)."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return False
            hits.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def client_ip(request: Request) -> str:
    """Адрес клиента; за nginx берём X-Forwarded-For, который nginx перезаписывает сам."""
    if request.app.state.settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def login_rate_limit(request: Request) -> None:
    if not request.app.state.login_limiter.check(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много попыток входа. Подождите минуту.",
                            headers={"Retry-After": "60"})
