import logging
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from supabase import Client, create_client

from config import Settings, get_settings

logger = logging.getLogger(__name__)


def get_supabase(settings: Settings = Depends(get_settings)) -> Client:
    """Returns a Supabase admin client (service role)."""
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase is not configured on this server.",
        )
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


async def get_current_user(
    authorization: Optional[str] = Header(None),
    settings: Settings = Depends(get_settings),
) -> dict:
    """
    Validates the Supabase JWT from the Authorization header and returns user info.
    Raises 401 if missing or invalid.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header.",
        )

    token = authorization.removeprefix("Bearer ").strip()

    try:
        supabase = create_client(
            settings.supabase_url, settings.supabase_service_role_key
        )
        user_response = supabase.auth.get_user(token)
        if not user_response or not user_response.user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token.",
            )
        return {"id": user_response.user.id, "email": user_response.user.email}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Auth validation error: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed.",
        )


async def get_current_admin(
    user: dict = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:

    try:
        supabase = create_client(
            settings.supabase_url, settings.supabase_service_role_key
        )

        user_id = str(user.get("id") or user.get("sub")) 

        result = (
            supabase.table("admins")
            .select("user_id")
            .eq("user_id", user_id)
            .execute()
        )

        if not result.data:
            raise HTTPException(
                status_code=403,
                detail="Not enough privileges.",
            )

        return user

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Admin validation error: {e}")
        raise HTTPException(
            status_code=500,
            detail="Error validating admin status.",
        )

async def get_optional_user(
    authorization: Optional[str] = Header(None),
    settings: Settings = Depends(get_settings),
) -> Optional[dict]:
    """
    Same as get_current_user but returns None instead of raising 401.
    Use for routes that work for both guests and authenticated users.
    """
    if not authorization:
        return None
    try:
        return await get_current_user(authorization, settings)
    except HTTPException:
        return None
