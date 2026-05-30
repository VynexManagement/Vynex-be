"""
Public catalog endpoints for filter options (no admin auth).
"""

from fastapi import APIRouter, Depends
from supabase import Client

from dependencies import get_supabase

router = APIRouter(prefix="/api", tags=["Catalog"])


@router.get("/catalog")
async def get_catalog(supabase: Client = Depends(get_supabase)):
    """
    Distinct niches and countries from stores, plus active signals for multi-select UIs.
    """
    niches: list[str] = []
    countries: list[str] = []
    signals: list[dict] = []

    try:
        n_res = supabase.table("stores").select("niche").execute()
        seen_n = set()
        for row in n_res.data or []:
            v = (row.get("niche") or "").strip()
            if v and v not in seen_n:
                seen_n.add(v)
                niches.append(v)
        niches.sort(key=str.lower)
    except Exception:
        pass

    try:
        c_res = supabase.table("stores").select("country").execute()
        seen_c = set()
        for row in c_res.data or []:
            v = (row.get("country") or "").strip()
            if v and v not in seen_c:
                seen_c.add(v)
                countries.append(v)
        countries.sort(key=str.lower)
    except Exception:
        pass

    try:
        s_res = (
            supabase.table("signals")
            .select("id, name, description")
            .order("name")
            .execute()
        )
        for row in s_res.data or []:
            signals.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "description": row.get("description"),
                }
            )
    except Exception:
        # Table missing or RLS — fall back to empty; frontend still has static fallbacks
        signals = []

    return {"niches": niches, "countries": countries, "signals": signals}
