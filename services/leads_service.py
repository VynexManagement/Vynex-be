import hashlib
import json
import logging
from typing import Any, Optional

from fastapi import HTTPException
from supabase import Client

from models.requests import QueryRequest
from models.responses import LeadPreview, PreviewResponse

logger = logging.getLogger(__name__)

DEFAULT_PRICE_INR = 3999
DEFAULT_PRICE_USD = 49


def _canonical_hash(
    niches: list[str],
    countries: list[str],
    signal_ids: list[str],
    signal_texts: list[str],
) -> str:
    payload = json.dumps(
        {
            "niches": sorted(niches),
            "countries": sorted(countries),
            "signal_ids": sorted(signal_ids),
            "signal_texts": sorted(signal_texts),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _normalize_filters(query: QueryRequest) -> tuple[list[str], list[str], list[str]]:
    niches = [x.strip() for x in query.niches if x and str(x).strip()]
    countries = [x.strip() for x in query.countries if x and str(x).strip()]
    signal_ids = [x.strip() for x in query.signal_ids if x and str(x).strip()]

    if query.niche and str(query.niche).strip():
        niches = list(dict.fromkeys(niches + [query.niche.strip()]))
    if query.country and str(query.country).strip():
        countries = list(dict.fromkeys(countries + [query.country.strip()]))
    if query.signal_id and str(query.signal_id).strip():
        signal_ids = list(dict.fromkeys(signal_ids + [query.signal_id.strip()]))

    return niches, countries, signal_ids


async def _merge_signal_names_into_ids(
    supabase: Client, signal_ids: list[str], names: list[str]
) -> tuple[list[str], list[str]]:
    """Resolve extra signal names to UUIDs; return (ids, unresolved names for text fallback)."""
    merged = list(dict.fromkeys(signal_ids))
    unresolved: list[str] = []
    for raw in names:
        name = raw.strip() if raw else ""
        if not name:
            continue
        sid = await _resolve_signal_name_to_id(supabase, name)
        if sid:
            merged = list(dict.fromkeys(merged + [sid]))
        else:
            unresolved.append(name)
    return merged, unresolved


async def _resolve_signal_name_to_id(supabase: Client, name: str) -> Optional[str]:
    try:
        res = supabase.table("signals").select("id").eq("name", name).limit(1).execute()
        if res.data:
            return res.data[0]["id"]
    except Exception as e:
        logger.warning("Could not resolve signal name %r: %s", name, e)
    return None


def _validate_filters(
    niches: list[str],
    countries: list[str],
    signal_ids: list[str],
    extra_signal_names: list[str],
) -> None:
    if not niches and not countries and not signal_ids and not extra_signal_names:
        raise HTTPException(
            400,
            "Select at least one niche, country, or marketing signal.",
        )


async def _store_ids_for_location(
    supabase: Client, niches: list[str], countries: list[str]
) -> Optional[list[str]]:
    """
    If either niche or country filters are set, return matching store IDs.
    If neither is set, return None (do not filter by store).
    """
    if not niches and not countries:
        return None

    q = supabase.table("stores").select("id")
    if niches:
        q = q.in_("niche", niches)
    if countries:
        q = q.in_("country", countries)
    try:
        res = q.execute()
    except Exception as e:
        logger.error(f"DB error fetching stores: {e}")
        raise HTTPException(500, "Database error while filtering stores.")

    return [row["id"] for row in (res.data or [])]


def _apply_lead_filters(
    query: Any,
    store_ids: Optional[list[str]],
    signal_ids: list[str],
    signal_texts: list[str],
):
    if store_ids is not None:
        query = query.in_("store_id", store_ids)
    if signal_ids:
        query = query.in_("signal_id", signal_ids)
    elif signal_texts:
        query = query.in_("signal", signal_texts)
    return query


async def _count_leads(
    supabase: Client,
    store_ids: Optional[list[str]],
    signal_ids: list[str],
    signal_texts: list[str],
) -> int:
    q = supabase.table("leads").select("id", count="exact")
    q = _apply_lead_filters(q, store_ids, signal_ids, signal_texts)
    try:
        res = q.limit(0).execute()
        return res.count or 0
    except Exception as e:
        logger.error(f"DB error counting leads: {e}")
        raise HTTPException(500, "Database error while counting leads.")


async def _fetch_preview_items(
    supabase: Client,
    store_ids: Optional[list[str]],
    signal_ids: list[str],
    signal_texts: list[str],
    niches: list[str],
) -> list[LeadPreview]:
    q = supabase.table("leads").select(
        "*, stores:stores!fk_leads_store(name, url, country, niche), signals(name)"
    )
    q = _apply_lead_filters(q, store_ids, signal_ids, signal_texts)
    q = q.limit(10)
    try:
        res = q.execute()
    except Exception:
        q2 = supabase.table("leads").select(
            "*, stores:stores!fk_leads_store(name, url, country, niche)"
        )
        q2 = _apply_lead_filters(q2, store_ids, signal_ids, signal_texts)
        q2 = q2.limit(10)
        res = q2.execute()

    items: list[LeadPreview] = []
    for lead in res.data or []:
        store = lead.get("stores") or {}
        sig = lead.get("signals") or {}
        signal_label = sig.get("name") or lead.get("signal") or ""
        items.append(
            LeadPreview(
                store_name=store.get("name", ""),
                url=store.get("url", ""),
                country=store.get("country", ""),
                niche=store.get("niche") or (niches[0] if niches else None),
                signal=signal_label,
            )
        )
    return items


async def _signal_labels(supabase: Client, signal_ids: list[str]) -> list[str]:
    if not signal_ids:
        return []
    try:
        res = supabase.table("signals").select("id, name").in_("id", signal_ids).execute()
        id_to_name = {row["id"]: row["name"] for row in (res.data or [])}
        return [id_to_name[i] for i in signal_ids if i in id_to_name]
    except Exception:
        return []


async def _ensure_dataset(
    supabase: Client,
    query_hash: str,
    niches: list[str],
    countries: list[str],
    signal_ids: list[str],
    signal_texts: list[str],
    signal_names: list[str],
    total_leads: int,
    store_ids: Optional[list[str]],
) -> str:
    niche_label = ", ".join(niches) if niches else "All niches"
    country_label = ", ".join(countries) if countries else "All countries"
    signal_label = ", ".join(signal_names) if signal_names else "All signals"

    row = {
        "query_hash": query_hash,
        "niche": niche_label,
        "country": country_label,
        "signal_id": signal_ids[0] if signal_ids else None,
        "total_leads": total_leads,
        "price_inr": DEFAULT_PRICE_INR,
        "price_usd": DEFAULT_PRICE_USD,
        "description": f"Leads matching filters: {niche_label} | {country_label} | {signal_label}",
    }

    try:
        existing = (
            supabase.table("datasets")
            .select("id")
            .eq("query_hash", query_hash)
            .execute()
        )
        if existing.data:
            dataset_id = existing.data[0]["id"]
            supabase.table("datasets").update(
                {
                    "niche": row["niche"],
                    "country": row["country"],
                    "signal_id": row["signal_id"],
                    "total_leads": total_leads,
                    "price_inr": DEFAULT_PRICE_INR,
                    "price_usd": DEFAULT_PRICE_USD,
                    "description": row["description"],
                }
            ).eq("id", dataset_id).execute()
        else:
            supabase.table("datasets").insert(row).execute()
            ds_new = (
                supabase.table("datasets")
                .select("id")
                .eq("query_hash", query_hash)
                .execute()
            )
            if not ds_new.data:
                raise HTTPException(500, "Could not create dataset row.")
            dataset_id = ds_new.data[0]["id"]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Dataset save failed: {e}")
        raise HTTPException(500, "Could not save dataset for checkout.")

    try:
        supabase.table("dataset_leads").delete().eq("dataset_id", dataset_id).execute()
    except Exception as e:
        logger.error(f"Failed clearing dataset_leads: {e}")

    # Paginate lead IDs and bulk-link
    page_size = 500
    offset = 0
    if store_ids is not None and len(store_ids) == 0:
        return dataset_id

    while True:
        q = supabase.table("leads").select("id")
        q = _apply_lead_filters(q, store_ids, signal_ids, signal_texts)
        q = q.range(offset, offset + page_size - 1)
        try:
            batch = q.execute()
        except Exception as e:
            logger.error(f"Failed paging leads for dataset: {e}")
            raise HTTPException(500, "Could not link leads to dataset.")

        rows = batch.data or []
        if not rows:
            break

        payload = [{"dataset_id": dataset_id, "lead_id": r["id"]} for r in rows]
        try:
            supabase.table("dataset_leads").insert(payload).execute()
        except Exception as e:
            logger.error(f"dataset_leads insert failed: {e}")
            raise HTTPException(500, "Could not link leads to dataset.")

        if len(rows) < page_size:
            break
        offset += page_size

    try:
        supabase.table("datasets").update({"total_leads": total_leads}).eq(
            "id", dataset_id
        ).execute()
    except Exception as e:
        logger.warning(f"Could not refresh total_leads on dataset: {e}")

    return dataset_id


async def get_preview(supabase: Client, query: QueryRequest) -> PreviewResponse:
    niches, countries, signal_ids = _normalize_filters(query)

    extra_names = [n.strip() for n in query.signal_names if n and str(n).strip()]
    if query.signal and str(query.signal).strip():
        extra_names.append(query.signal.strip())

    _validate_filters(niches, countries, signal_ids, extra_names)

    merged_ids, unresolved = await _merge_signal_names_into_ids(
        supabase, signal_ids, extra_names
    )
    signal_texts = list(dict.fromkeys(unresolved)) if not merged_ids else []

    store_ids = await _store_ids_for_location(supabase, niches, countries)

    if merged_ids:
        signal_names = await _signal_labels(supabase, merged_ids)
    else:
        signal_names = signal_texts

    if store_ids is not None and len(store_ids) == 0:
        return PreviewResponse(
            dataset_id="",
            items=[],
            total_count=0,
            price_inr=DEFAULT_PRICE_INR,
            price_usd=DEFAULT_PRICE_USD,
            niche=niches[0] if len(niches) == 1 else None,
            country=countries[0] if len(countries) == 1 else None,
            signal=signal_names[0] if len(signal_names) == 1 else None,
            niches=niches,
            countries=countries,
            signal_ids=merged_ids,
            signal_names=signal_names,
        )

    total_count = await _count_leads(supabase, store_ids, merged_ids, signal_texts)
    items = await _fetch_preview_items(
        supabase, store_ids, merged_ids, signal_texts, niches
    )

    qh = _canonical_hash(niches, countries, merged_ids, signal_texts)
    dataset_id = ""
    if query.persist and total_count > 0:
        dataset_id = await _ensure_dataset(
            supabase,
            qh,
            niches,
            countries,
            merged_ids,
            signal_texts,
            signal_names,
            total_count,
            store_ids,
        )

    return PreviewResponse(
        dataset_id=dataset_id,
        items=items,
        total_count=total_count,
        price_inr=DEFAULT_PRICE_INR,
        price_usd=DEFAULT_PRICE_USD,
        niche=niches[0] if len(niches) == 1 else None,
        country=countries[0] if len(countries) == 1 else None,
        signal=signal_names[0] if len(signal_names) == 1 else None,
        niches=niches,
        countries=countries,
        signal_ids=merged_ids,
        signal_names=signal_names,
    )
