from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from supabase import Client

from dependencies import get_current_user, get_supabase
from models.requests import RenamePurchaseRequest
from services import download_service

router = APIRouter(prefix="/api", tags=["Downloads"])


@router.get("/download-leads/{dataset_id}")
async def download_leads(
    dataset_id: str,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
) -> StreamingResponse:
    """
    Auth-gated CSV download. Verifies the user has a completed purchase for
    this dataset before streaming the file.
    """
    return await download_service.stream_csv(dataset_id, user["id"], supabase)


@router.get("/purchases")
async def get_purchases(
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
) -> list:
    """Returns all completed purchases for the authenticated user."""
    return await download_service.get_user_purchases(user["id"], supabase)


@router.patch("/purchases/{purchase_id}/rename")
async def rename_purchase(
    purchase_id: str,
    req: RenamePurchaseRequest,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """Rename a purchased dataset by purchase ID."""
    return await download_service.rename_purchase(purchase_id, user["id"], req.name, supabase)


@router.get("/purchases/{purchase_id}/leads")
async def get_purchase_leads(
    purchase_id: str,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
) -> list:
    """Returns all leads for a completed purchase."""
    return await download_service.get_purchase_leads(purchase_id, user["id"], supabase)
