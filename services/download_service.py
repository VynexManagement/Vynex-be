import csv
import io
import logging

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from supabase import Client

logger = logging.getLogger(__name__)


async def stream_csv(
    dataset_id: str, user_id: str, supabase: Client
) -> StreamingResponse:
    """
    Auth-gated CSV export. Verifies the user has a completed purchase for this
    dataset before streaming the CSV.
    """
    # ── Authorisation check ──
    try:
        purchase_res = (
            supabase.table("purchases")
            .select("id")
            .eq("user_id", user_id)
            .eq("dataset_id", dataset_id)
            .eq("status", "completed")
            .execute()
        )
    except Exception as e:
        logger.error(f"DB error checking purchase auth: {e}")
        raise HTTPException(status_code=500, detail="Database error.")

    if not purchase_res.data:
        raise HTTPException(
            status_code=403,
            detail="You have not purchased this dataset.",
        )

    # ── Fetch all leads ──
    try:
        dl_response = (
            supabase.table("dataset_leads")
            .select("lead_id")
            .eq("dataset_id", dataset_id)
            .execute()
        )
    except Exception as e:
        logger.error(f"DB error fetching dataset_leads: {e}")
        raise HTTPException(status_code=500, detail="Database error fetching leads.")

    lead_ids = [row["lead_id"] for row in dl_response.data]

    if not lead_ids:
        raise HTTPException(status_code=404, detail="No leads found for this dataset.")

    try:
        leads_res = (
            supabase.table("leads")
            .select("signal, stores(name, url, country, niche, product_count, avg_price)")
            .in_("id", lead_ids)
            .execute()
        )
    except Exception as e:
        logger.error(f"DB error fetching leads: {e}")
        raise HTTPException(status_code=500, detail="Database error building CSV.")

    # ── Build CSV ──
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(
        ["Store Name", "Store URL", "Country", "Niche", "Signal", "Product Count", "Avg Price (USD)"]
    )

    for lead in leads_res.data:
        store = lead.get("stores")
        if store:
            writer.writerow(
                [
                    store.get("name", ""),
                    store.get("url", ""),
                    store.get("country", ""),
                    store.get("niche", ""),
                    lead.get("signal", ""),
                    store.get("product_count", ""),
                    store.get("avg_price", ""),
                ]
            )

    stream.seek(0)
    csv_content = stream.getvalue()

    response = StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
    )
    response.headers["Content-Disposition"] = (
        f'attachment; filename="leads_{dataset_id[:8]}.csv"'
    )
    return response


async def get_user_purchases(user_id: str, supabase: Client) -> list:
    """Returns all completed purchases for a user, joined with dataset metadata."""
    try:
        res = (
            supabase.table("purchases")
            .select(
                "id, dataset_id, payment_method, status, created_at, "
                "datasets(niche, country, signal_id, total_leads, price_inr, price_usd)"
            )
            .eq("user_id", user_id)
            .eq("status", "completed")
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as e:
        logger.error(f"DB error fetching purchases for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch purchases.")

    signal_ids = {
        d.get("signal_id")
        for d in ((p.get("datasets") or {}) for p in (res.data or []))
        if d.get("signal_id")
    }
    signal_map = {}
    if signal_ids:
        try:
            sig_res = (
                supabase.table("signals")
                .select("id, name")
                .in_("id", list(signal_ids))
                .execute()
            )
            signal_map = {row["id"]: row.get("name", "") for row in (sig_res.data or [])}
        except Exception as e:
            logger.warning(f"Could not resolve signal names for purchases: {e}")

    purchases = []
    for p in res.data:
        d = p.get("datasets") or {}
        purchases.append(
            {
                "id": p["id"],
                "dataset_id": p["dataset_id"],
                "niche": d.get("niche", ""),
                "country": d.get("country", ""),
                "signal": signal_map.get(d.get("signal_id"), "Unknown Signal"),
                "total_leads": d.get("total_leads", 0),
                "price_inr": d.get("price_inr"),
                "price_usd": d.get("price_usd"),
                "payment_method": p.get("payment_method"),
                "purchase_date": p["created_at"],
            }
        )
    return purchases
