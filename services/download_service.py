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
            .select("stores:stores!fk_leads_store(name, url, country, niche, product_count, avg_price), signals(name)")
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
        sig = lead.get("signals") or {}
        signal_name = sig.get("name") or lead.get("signal") or ""
        if store:
            writer.writerow(
                [
                    store.get("name", ""),
                    store.get("url", ""),
                    store.get("country", ""),
                    store.get("niche", ""),
                    signal_name,
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
                "id, dataset_id, payment_method, status, created_at, name, "
                "datasets(niche, country, signal_id, total_leads, price_inr, price_usd)"
            )
            .eq("user_id", user_id)
            .eq("status", "completed")
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as e:
        err_msg = str(e)
        if "42703" in err_msg or "column purchases.name" in err_msg or "name" in err_msg:
            logger.info("Missing 'name' column in purchases table. Falling back to query without 'name'.")
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
            except Exception as ex:
                logger.error(f"Fallback DB query failed: {ex}")
                raise HTTPException(status_code=500, detail="Failed to fetch purchases.")
        else:
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
                "name": p.get("name"),
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


async def rename_purchase(
    purchase_id: str, user_id: str, name: str, supabase: Client
) -> dict:
    """Renames a purchase record for the user, verifying ownership."""
    try:
        check_res = (
            supabase.table("purchases")
            .select("id")
            .eq("id", purchase_id)
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as e:
        logger.error(f"DB error checking purchase for rename: {e}")
        raise HTTPException(status_code=500, detail="Database error.")

    if not check_res.data:
        raise HTTPException(
            status_code=404,
            detail="Purchase not found or access denied.",
        )

    try:
        supabase.table("purchases").update({"name": name}).eq("id", purchase_id).execute()
    except Exception as e:
        err_msg = str(e)
        if "42703" in err_msg or "column purchases.name" in err_msg or "name" in err_msg:
            raise HTTPException(
                status_code=400,
                detail="Dataset renaming is disabled because the database schema has not been updated. "
                       "Please run the SQL migration (02_add_name_to_purchases.sql) in your Supabase editor."
            )
        logger.error(f"DB error renaming purchase {purchase_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to rename purchase.")

    return {"success": True, "message": "Purchase renamed successfully."}


async def get_purchase_leads(
    purchase_id: str, user_id: str, supabase: Client
) -> list:
    """Returns all leads for a purchased dataset, joined with store and signal info."""
    try:
        purchase_res = (
            supabase.table("purchases")
            .select("dataset_id")
            .eq("id", purchase_id)
            .eq("user_id", user_id)
            .eq("status", "completed")
            .execute()
        )
    except Exception as e:
        logger.error(f"DB error checking purchase for leads: {e}")
        raise HTTPException(status_code=500, detail="Database error.")

    if not purchase_res.data:
        raise HTTPException(
            status_code=403,
            detail="You have not purchased this dataset or it does not exist.",
        )

    dataset_id = purchase_res.data[0]["dataset_id"]

    try:
        dl_response = (
            supabase.table("dataset_leads")
            .select("lead_id")
            .eq("dataset_id", dataset_id)
            .execute()
        )
    except Exception as e:
        logger.error(f"DB error fetching dataset_leads: {e}")
        raise HTTPException(status_code=500, detail="Database error.")

    lead_ids = [row["lead_id"] for row in dl_response.data]
    if not lead_ids:
        return []

    try:
        leads_res = (
            supabase.table("leads")
            .select(
                "id, "
                "stores:stores!fk_leads_store(name, url, country, niche, product_count, avg_price), "
                "signals(name)"
            )
            .in_("id", lead_ids)
            .execute()
        )
    except Exception as e:
        logger.error(f"DB error fetching lead details: {e}")
        raise HTTPException(status_code=500, detail="Database error.")

    leads = []
    for lead in (leads_res.data or []):
        store = lead.get("stores") or {}
        sig = lead.get("signals") or {}
        signal_name = sig.get("name") or lead.get("signal") or ""
        leads.append({
            "id": lead["id"],
            "store_name": store.get("name", ""),
            "url": store.get("url", ""),
            "country": store.get("country", ""),
            "niche": store.get("niche", ""),
            "signal": signal_name,
            "product_count": store.get("product_count"),
            "avg_price": store.get("avg_price"),
        })
    return leads

