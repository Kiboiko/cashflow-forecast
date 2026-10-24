"""POST /auth/login, демо-персоны и вход одним нажатием (§9.1 экран 1, §10)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from app.db.models import Business, User
from app.db.session import DbSession
from app.schemas import (
    DemoLoginRequest,
    DemoPersona,
    JuryCompany,
    JuryLoginRequest,
    LoginRequest,
    TokenResponse,
    UserOut,
)
from app.security import create_access_token, hash_password, login_rate_limit, verify_password
from app.services import jury_service
from app.services.personas import JURY_EMAIL_PREFIX

router = APIRouter(tags=["auth"])

_DUMMY_HASH = hash_password("dummy-password-for-timing")      # чтобы неизвестный email отвечал за то же время


def user_out(user: User) -> UserOut:
    return UserOut(id=user.id, email=user.email, display_name=user.display_name,
                   telegram_connected=bool(user.telegram_chat_id))


def _token(request: Request, user: User) -> TokenResponse:
    return TokenResponse(access_token=create_access_token(user.id, request.app.state.settings), user=user_out(user))


@router.post("/auth/login", response_model=TokenResponse, dependencies=[Depends(login_rate_limit)])
def login(body: LoginRequest, request: Request, db: DbSession) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if user is None or not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный email или пароль")
    return _token(request, user)


def _require_demo(request: Request) -> None:
    if not request.app.state.settings.demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Не найдено")


@router.get("/auth/demo-personas", response_model=list[DemoPersona], dependencies=[Depends(_require_demo)])
def demo_personas(db: DbSession) -> list[DemoPersona]:
    """Карточки персон: показываем только тех, у кого есть бизнес, в порядке создания."""
    users = db.scalars(select(User).where(User.is_demo.is_(True), ~User.email.startswith(JURY_EMAIL_PREFIX))
                       .order_by(User.id)).all()          # компании режима жюри карточками не показываем
    out = []
    for user in users:
        business = db.scalar(select(Business).where(Business.owner_user_id == user.id).order_by(Business.id))
        if business is None:
            continue
        title = " ".join(x for x in (business.industry.lower() if business.industry else None,
                                     f"({business.legal_form})" if business.legal_form else None) if x)
        out.append(DemoPersona(email=user.email, display_name=user.display_name, title=title or business.name,
                               note=user.persona_note, business_id=business.id))
    return out


@router.post("/auth/demo-login", response_model=TokenResponse, dependencies=[Depends(_require_demo)])
def demo_login(body: DemoLoginRequest, request: Request, db: DbSession) -> TokenResponse:
    """Вход персоны без пароля — только для демо-пользователей и только при DEMO_MODE=true."""
    user = db.scalar(select(User).where(User.email == body.email.strip().lower(), User.is_demo.is_(True)))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Не найдено")
    return _token(request, user)


@router.get("/auth/jury-companies", response_model=list[JuryCompany], dependencies=[Depends(_require_demo)])
def jury_companies(request: Request) -> list[JuryCompany]:
    """Режим жюри: компании датасета, которые можно открыть. Нет датасета на сервере — пустой список."""
    return jury_service.list_companies(jury_service.raw_dir(request.app.state.settings))


@router.post("/auth/jury-login", response_model=TokenResponse, dependencies=[Depends(_require_demo)])
def jury_login(body: JuryLoginRequest, request: Request, db: DbSession) -> TokenResponse:
    """Вход в любую компанию датасета как в отдельного предпринимателя; первый вход загружает её выписку (до минуты)."""
    try:
        user = jury_service.open_company(db, request.app.state.forecast_service, request.app.state.settings, body.company_id)
    except jury_service.JuryError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return _token(request, user)
