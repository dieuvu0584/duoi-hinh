from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import client_ip, current_owner
from app.core.errors import ApiError
from app.db.models import OwnerAccount
from app.db.session import get_session
from app.services import auth_service, budget_service

router = APIRouter(tags=["costs"])

PROVIDERS = {"anthropic", "fal", "elevenlabs", "gemini"}


class ProviderBudgetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    monthly_limit_usd: Decimal = Field(ge=0, max_digits=12, decimal_places=4)
    daily_limit_usd: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=4)
    password: str | None = Field(default=None, max_length=256)


@router.get("/costs")
def costs(owner: OwnerAccount = Depends(current_owner), session: Session = Depends(get_session)):
    return budget_service.summary(session)


@router.put("/system/provider-budgets")
def put_provider_budget(
    body: ProviderBudgetRequest,
    request: Request,
    owner: OwnerAccount = Depends(current_owner),
    session: Session = Depends(get_session),
):
    if body.provider not in PROVIDERS:
        allowed = ",".join(sorted(PROVIDERS))
        raise ApiError(422, "unknown_provider", "Unknown provider",
                       fields=[{"field": "provider", "issue": f"not_in_{allowed}"}])
    if budget_service.raises_limit(session, body.provider, body.monthly_limit_usd,
                                   body.daily_limit_usd):
        if not body.password:
            raise ApiError(403, "reauth_required", "Password confirmation required")
        auth_service.verify_owner_password(session, owner, body.password, client_ip(request))
    row = budget_service.set_provider_budget(
        session, provider=body.provider, monthly_limit_usd=body.monthly_limit_usd,
        daily_limit_usd=body.daily_limit_usd, actor=f"owner:{owner.id}")
    return {"provider": row.provider, "monthly_limit_usd": str(row.monthly_limit_usd),
            "daily_limit_usd": None if row.daily_limit_usd is None else str(row.daily_limit_usd)}
