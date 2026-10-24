"""GET /me — пользователь и его бизнесы."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.auth import user_out
from app.schemas import BusinessBrief, MeResponse
from app.security import CurrentUser

router = APIRouter(tags=["me"])


@router.get("/me", response_model=MeResponse)
def me(user: CurrentUser) -> MeResponse:
    return MeResponse(
        user=user_out(user),
        businesses=[BusinessBrief(id=b.id, name=b.name, legal_form=b.legal_form, industry=b.industry,
                                  data_as_of=b.data_as_of) for b in user.businesses])
