from fastapi import APIRouter, Depends
from supabase import Client

from dependencies import get_supabase
from models.requests import QueryRequest
from models.responses import PreviewResponse
from services import leads_service

router = APIRouter(prefix="/api", tags=["Leads"])


@router.post("/get-leads-preview", response_model=PreviewResponse)
async def get_leads_preview(
    query: QueryRequest,
    supabase: Client = Depends(get_supabase),
):
    """
    Query leads by niche, country, and signal.
    Returns up to 10 preview rows + total count and pricing.
    """
    return await leads_service.get_preview(supabase, query)
