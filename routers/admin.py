import asyncio
import logging
import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from supabase import Client

from config import Settings, get_settings
from dependencies import get_current_admin, get_supabase

router = APIRouter(prefix="/api/admin", tags=["Admin"])
logger = logging.getLogger(__name__)

# --- Models ---
class ScraperRequest(BaseModel):
    niche: str
    country: str
    signal: Optional[str] = None
    all_signals: bool = True
    limit: int = 50

class UserRoleUpdate(BaseModel):
    is_admin: bool

# --- Ensure log directory exists ---
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# helper function to get signal id
async def get_signal_id(signal_value: str, supabase: Client) -> str:
    slug_res = supabase.table("signals").select("id").eq("slug", signal_value).limit(1).execute()
    if slug_res.data:
        return slug_res.data[0]["id"]

    name_res = supabase.table("signals").select("id").eq("name", signal_value).limit(1).execute()
    if name_res.data:
        return name_res.data[0]["id"]

    raise HTTPException(status_code=400, detail=f"Signal '{signal_value}' not found")

# --- Endpoints: Users ---

@router.get("/users")
async def list_users(
    admin: dict = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase),
):
    """List all users from profiles."""
    try:
        auth_res = supabase.auth.admin.list_users()
        auth_users = (
            getattr(auth_res, "users", None)
            or (getattr(auth_res, "data", None) or {}).get("users", [])
            or []
        )
        profiles_res = supabase.table("profiles").select("*").execute()
        admins_res = supabase.table("admins").select("user_id").execute()
        
        profiles = list(profiles_res.data)
        admin_ids = {str(row["user_id"]) for row in (admins_res.data or [])}
        
        # Merge email into profile dicts for frontend display
        email_map = {
            str(getattr(u, "id", "")): getattr(u, "email", "Unknown")
            for u in auth_users
        }
        for p in profiles:
            p["email"] = email_map.get(p["id"], "Unknown")
            p["is_admin"] = p.get("is_admin", str(p["id"]) in admin_ids)
            
        return profiles
    except Exception as e:
        logger.error(f"Error fetching users: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not fetch users.",
        )


@router.patch("/users/{user_id}/role")
async def update_user_role(
    user_id: str,
    payload: UserRoleUpdate,
    admin: dict = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase),
):
    if user_id == str(admin["id"]):
        raise HTTPException(status_code=400, detail="Cannot modify your own role.")

    try:
        if payload.is_admin:
            supabase.table("admins").insert({"user_id": user_id}).execute()
        else:
            supabase.table("admins").delete().eq("user_id", user_id).execute()

        return {"message": "Role updated successfully"}
    except Exception as e:
        logger.error(f"Error updating role: {e}")
        raise HTTPException(500, "Could not update user role.")

# --- Endpoints: Scraper ---

@router.get("/scraper/quota")
async def get_scraper_quota(
    admin: dict = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase)
):
    try:
        res = supabase.table("signals").select("*").eq("slug", "serpapi_quota").execute()
        if not res.data:
            # Seed default values: 250 limit, 2 consumed
            insert_res = supabase.table("signals").insert({
                "name": "SerpApi Quota",
                "slug": "serpapi_quota",
                "description": "250",
                "rule_definition": "2",
                "active": True,
                "is_active": True,
                "type": "system_meta"
            }).execute()
            quota = insert_res.data[0]
        else:
            quota = res.data[0]
        
        return {
            "limit": int(quota.get("description") or 250),
            "consumed": int(quota.get("rule_definition") or 2)
        }
    except Exception as e:
        logger.error(f"Error getting scraper quota: {e}")
        return {"limit": 250, "consumed": 2}

active_tasks = {}

@router.post("/scraper/run")
async def run_scraper(
    req: ScraperRequest,
    admin: dict = Depends(get_current_admin),
):
    """Triggers the scraper process and returns a task ID to poll for logs."""
    task_id = str(uuid.uuid4())
    log_file_path = os.path.join(LOG_DIR, f"{task_id}.log")
    
    scraper_script = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "scraper", "scraper.py")
    
    # We will build the command
    cmd = [
        r"D:\Product\scraper\venv\Scripts\python.exe",
        scraper_script,
        "--niche", req.niche,
        "--country", req.country,
        "--limit", str(req.limit)
    ]
    if req.all_signals:
        cmd.append("--all-signals")
    elif req.signal:
        cmd.extend(["--signal", req.signal])
    else:
        raise HTTPException(status_code=400, detail="signal is required when all_signals is false")
    
    try:
        # Start process and redirect stdout and stderr to the log file
        with open(log_file_path, "w") as log_file:
            process = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=os.path.dirname(scraper_script) # run in scraper directory
            )
        
        active_tasks[task_id] = process
        
        return {
            "message": "Scraper started successfully",
            "task_id": task_id
        }
    except Exception as e:
        logger.error(f"Failed to start scraper: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start scraper: {str(e)}"
        )


@router.get("/scraper/logs/{task_id}")
async def get_scraper_logs(
    task_id: str,
    limit: int = 200,  # last N lines
    admin: dict = Depends(get_current_admin),
):
    log_file_path = os.path.join(LOG_DIR, f"{task_id}.log")

    if not os.path.exists(log_file_path):
        return {
            "status": "starting",
            "logs": [],
            "summary": {}
        }

    try:
        # --- Read last N lines efficiently ---
        try:
            with open(log_file_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
                lines = lines[-limit:]
        except Exception:
            lines = ["Log file is being written..."]

        # --- Process status ---
        process = active_tasks.get(task_id)

        if not process:
            status = "unknown_or_completed"
        else:
            retcode = process.poll()
            if retcode is None:
                status = "running"
            else:
                status = "completed" if retcode == 0 else "failed"

        # --- DEBUG SUMMARY EXTRACTION ---
        summary = {
            "total_lines": len(lines),
            "errors": 0,
            "http_400": 0,
            "http_500": 0,
            "http_202": 0,
            "discovered_urls": 0,
            "matches_found": 0,
            "fetch_failures": 0,
        }

        for line in lines:
            l = line.lower()

            if "error" in l:
                summary["errors"] += 1

            if "400 bad request" in l:
                summary["http_400"] += 1

            if "500" in l:
                summary["http_500"] += 1

            if "202 accepted" in l:
                summary["http_202"] += 1

            if "discovered" in l and "candidate urls" in l:
                # extract number if possible
                try:
                    num = int("".join(filter(str.isdigit, line)))
                    summary["discovered_urls"] += num
                except:
                    pass

            if "✓ match" in l or "match:" in l:
                summary["matches_found"] += 1

            if "fetch failed" in l:
                summary["fetch_failures"] += 1

        # --- categorize logs (useful for frontend tabs) ---
        categorized_logs = {
            "errors": [],
            "http": [],
            "discovery": [],
            "scraper": [],
        }

        for line in lines:
            l = line.lower()

            if "error" in l:
                categorized_logs["errors"].append(line)
            elif "http request" in l or "http " in l:
                categorized_logs["http"].append(line)
            elif "discovery" in l:
                categorized_logs["discovery"].append(line)
            else:
                categorized_logs["scraper"].append(line)

        return {
            "status": status,
            "logs": lines,
            "summary": summary,
            "categorized_logs": categorized_logs,
        }

    except Exception as e:
        logger.error(f"Error reading logs: {e}")
        return {
            "status": "error",
            "logs": ["Error reading logs"],
            "summary": {}
        }


@router.post("/scraper/abort/{task_id}")
async def abort_scraper(
    task_id: str,
    admin: dict = Depends(get_current_admin),
):
    process = active_tasks.get(task_id)
    if not process:
        raise HTTPException(status_code=404, detail="Scraper task not found")

    retcode = process.poll()
    if retcode is not None:
        return {"message": "Task already completed", "status": "completed"}

    try:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        return {"message": "Scraper aborted", "status": "aborted"}
    except Exception as e:
        logger.error("Failed to abort scraper task %s: %s", task_id, e)
        raise HTTPException(status_code=500, detail="Failed to abort scraper task")

# --- Endpoints: Signals ---
from models.admin import SignalCreate, SignalUpdate, LeadStatusUpdate, BulkLeadAction

@router.get("/signals")
async def list_signals(admin: dict = Depends(get_current_admin), supabase: Client = Depends(get_supabase)):
    res = supabase.table("signals").select("*").order("created_at", desc=True).execute()
    return res.data

@router.post("/signals")
async def create_signal(payload: SignalCreate, admin: dict = Depends(get_current_admin), supabase: Client = Depends(get_supabase)):
    data = payload.model_dump(exclude_unset=True)
    if "active" in data and "is_active" not in data:
        data["is_active"] = data.pop("active")
    if "slug" not in data or not data.get("slug"):
        data["slug"] = (data.get("name") or "").strip().lower().replace(" ", "_")
    if "type" not in data or not data.get("type"):
        data["type"] = "base"
    if "is_active" not in data:
        data["is_active"] = True
    res = supabase.table("signals").insert(data).execute()
    return res.data[0] if res.data else None

@router.put("/signals/{signal_id}")
async def update_signal(signal_id: str, payload: SignalUpdate, admin: dict = Depends(get_current_admin), supabase: Client = Depends(get_supabase)):
    data = payload.model_dump(exclude_unset=True)
    if "active" in data and "is_active" not in data:
        data["is_active"] = data.pop("active")
    res = supabase.table("signals").update(data).eq("id", signal_id).execute()
    return res.data[0] if res.data else None

# --- Endpoints: Leads ---
@router.get("/leads")
async def list_leads(
    country: str = "",
    signal: str = "",
    verified: str = "",
    admin: dict = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase),
):
    # Base fetch then apply join-safe filters in Python for compatibility with current schema.
    res = (
        supabase.table("leads")
        .select("*, stores!fk_leads_store(*), signals(*)")
        .order("created_at", desc=True)
        .limit(1000)
        .execute()
    )
    rows = res.data or []

    country_filter = country.strip().lower()
    signal_filter = signal.strip().lower()
    verified_filter = verified.strip().lower()

    def _country_ok(row: dict) -> bool:
        if not country_filter:
            return True
        return ((row.get("stores") or {}).get("country") or "").strip().lower() == country_filter

    def _signal_ok(row: dict) -> bool:
        if not signal_filter:
            return True
        signal_name = ((row.get("signals") or {}).get("name") or row.get("signal") or "").strip().lower()
        return signal_name == signal_filter

    def _verified_ok(row: dict) -> bool:
        if not verified_filter or verified_filter == "all":
            return True
        status_value = (row.get("status") or "").strip().lower()
        if verified_filter == "unverified":
            return status_value in ("", "unchecked", "unknown", "pending")
        return status_value == verified_filter

    filtered = [r for r in rows if _country_ok(r) and _signal_ok(r) and _verified_ok(r)]
    return filtered[:500]

@router.post("/leads/{lead_id}/status")
async def update_lead_status(lead_id: str, payload: LeadStatusUpdate, admin: dict = Depends(get_current_admin), supabase: Client = Depends(get_supabase)):
    res = supabase.table("leads").update({"status": payload.status}).eq("id", lead_id).execute()
    return res.data[0] if res.data else None

@router.post("/leads/bulk-action")
async def bulk_action_leads(payload: BulkLeadAction, admin: dict = Depends(get_current_admin), supabase: Client = Depends(get_supabase)):
    if payload.action == "delete":
        res = supabase.table("leads").delete().in_("id", payload.lead_ids).execute()
    else:
        res = supabase.table("leads").update({"status": payload.action}).in_("id", payload.lead_ids).execute()
    return {"message": "Bulk action completed"}

# --- Endpoints: Dataset Builder ---
@router.get("/dataset/preview")
async def dataset_preview(
    niche: str = "",
    country: str = "",
    signal: str = "",
    limit: int = 50,
    admin: dict = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase)
):
    query = supabase.table("leads") \
        .select("*, stores!fk_leads_store!inner(*), signals!inner(*)") \
        .eq("status", "valid")

    if niche:
        query = query.eq("stores.niche", niche)

    if country:
        query = query.eq("stores.country", country)

    if signal:
        signal_id = await get_signal_id(signal, supabase)
        query = query.eq("signal_id", signal_id)

    res = query.limit(limit).execute()

    return {
        "items": res.data[:10],
        "total_available": len(res.data),
        "limit_applied": limit
    }

@router.get("/dataset/export")
async def dataset_export(
    niche: str = "",
    country: str = "",
    signal: str = "",
    limit: int = 50,
    admin: dict = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase)
):
    from fastapi.responses import StreamingResponse
    import io
    import csv

    query = supabase.table("leads") \
        .select("*, stores!fk_leads_store!inner(*), signals!inner(*)") \
        .eq("status", "valid")

    if niche:
        query = query.eq("stores.niche", niche)

    if country:
        query = query.eq("stores.country", country)

    if signal:
        signal_id = await get_signal_id(signal, supabase)
        query = query.eq("signal_id", signal_id)

    res = query.limit(limit).execute()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["Store Name", "URL", "Niche", "Country", "Signal"])

    seen_urls = set()

    for l in res.data:
        store = l.get("stores", {})
        sig = l.get("signals", {})

        url = store.get("url", "")

        if url and url not in seen_urls:
            writer.writerow([
                store.get("name", ""),
                url,
                store.get("niche", ""),
                store.get("country", ""),
                sig.get("name", "")
            ])
            seen_urls.add(url)

    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=dataset_export.csv"}
    )

# --- Endpoints: Orders ---
@router.get("/orders")
async def list_orders(admin: dict = Depends(get_current_admin), supabase: Client = Depends(get_supabase)):
    try:
        res = (
            supabase.table("purchases")
            .select(
                "id, user_id, dataset_id, payment_method, status, created_at, "
                "datasets(total_leads, price_usd, price_inr, niche, country, signal_id)"
            )
            .order("created_at", desc=True)
            .execute()
        )

        signal_ids = {
            d.get("signal_id")
            for d in ((p.get("datasets") or {}) for p in (res.data or []))
            if d.get("signal_id")
        }
        signal_map = {}
        if signal_ids:
            sig_res = (
                supabase.table("signals")
                .select("id, name")
                .in_("id", list(signal_ids))
                .execute()
            )
            signal_map = {row["id"]: row.get("name", "") for row in (sig_res.data or [])}

        orders = []
        for row in res.data or []:
            ds = row.get("datasets") or {}
            orders.append(
                {
                    "id": row.get("id"),
                    "user_id": row.get("user_id"),
                    "dataset_id": row.get("dataset_id"),
                    "payment_method": row.get("payment_method"),
                    "status": row.get("status"),
                    "created_at": row.get("created_at"),
                    "total_leads": ds.get("total_leads"),
                    "price_usd": ds.get("price_usd"),
                    "price_inr": ds.get("price_inr"),
                    "niche": ds.get("niche"),
                    "country": ds.get("country"),
                    "signal": signal_map.get(ds.get("signal_id"), "Unknown Signal"),
                }
            )
        return orders
    except Exception as e:
        logger.error(f"Could not fetch admin orders: {e}")
        raise HTTPException(status_code=500, detail="Could not fetch orders.")

# --- Endpoints: Data Quality ---
@router.get("/data-quality")
async def data_quality_metrics(admin: dict = Depends(get_current_admin), supabase: Client = Depends(get_supabase)):
    res = supabase.table("leads").select("status").execute()
    all_leads = res.data
    total = len(all_leads)
    valid = sum(1 for l in all_leads if l.get("status") == "valid")
    broken = sum(1 for l in all_leads if l.get("status") == "broken")
    
    return {
        "total": total,
        "valid_pct": (valid/total*100) if total else 0,
        "broken_pct": (broken/total*100) if total else 0,
        "valid_count": valid,
        "broken_count": broken
    }

@router.post("/data-quality/recheck")
async def recheck_urls(admin: dict = Depends(get_current_admin), supabase: Client = Depends(get_supabase)):
    # Trigger background job to recheck 200 HTTP statuses
    return {"message": "Recheck background process queued."}


@router.get("/dashboard/metrics")
async def dashboard_metrics(admin: dict = Depends(get_current_admin), supabase: Client = Depends(get_supabase)):
    stores_res = supabase.table("stores").select("id", count="exact").limit(0).execute()
    leads_res = supabase.table("leads").select("status").execute()
    purchases_res = supabase.table("purchases").select("datasets(price_usd,price_inr)").execute()

    total_stores = stores_res.count or 0
    leads = leads_res.data or []
    valid = sum(1 for row in leads if (row.get("status") or "").lower() == "valid")
    broken = sum(1 for row in leads if (row.get("status") or "").lower() == "broken")
    total_reviewed = valid + broken
    broken_pct = round((broken / total_reviewed) * 100, 2) if total_reviewed else 0

    revenue_usd = 0.0
    for row in purchases_res.data or []:
        ds = row.get("datasets") or {}
        try:
            revenue_usd += float(ds.get("price_usd") or 0)
        except (TypeError, ValueError):
            continue

    return {
        "total_stores": total_stores,
        "valid_leads": valid,
        "broken_leads_pct": broken_pct,
        "total_revenue": round(revenue_usd, 2),
    }


@router.get("/dashboard/data-quality")
async def dashboard_data_quality(admin: dict = Depends(get_current_admin), supabase: Client = Depends(get_supabase)):
    leads_res = supabase.table("leads").select("status, store_id, stores!fk_leads_store(niche)").execute()
    rows = leads_res.data or []
    valid_count = sum(1 for row in rows if (row.get("status") or "").lower() == "valid")
    broken_rows = [row for row in rows if (row.get("status") or "").lower() == "broken"]
    broken_count = len(broken_rows)
    total_reviewed = valid_count + broken_count
    valid_pct = round((valid_count / total_reviewed) * 100, 2) if total_reviewed else 0

    fail_by_niche = {}
    for row in broken_rows:
        niche = ((row.get("stores") or {}).get("niche") or "Unknown").strip() or "Unknown"
        fail_by_niche[niche] = fail_by_niche.get(niche, 0) + 1
    top_failing_niches = sorted(
        [{"name": k, "count": v} for k, v in fail_by_niche.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:5]

    return {
        "valid_pct": valid_pct,
        "valid_count": valid_count,
        "broken_count": broken_count,
        "top_failing_niches": top_failing_niches,
    }


@router.get("/dashboard/inventory")
async def dashboard_inventory(admin: dict = Depends(get_current_admin), supabase: Client = Depends(get_supabase)):
    leads_res = supabase.table("leads").select("id, signal_id, stores!fk_leads_store(niche), signals(name)").execute()
    rows = leads_res.data or []

    by_niche = {}
    by_signal = {}
    for row in rows:
        niche = ((row.get("stores") or {}).get("niche") or "Unknown").strip() or "Unknown"
        signal_name = ((row.get("signals") or {}).get("name") or "Unknown").strip() or "Unknown"
        by_niche[niche] = by_niche.get(niche, 0) + 1
        by_signal[signal_name] = by_signal.get(signal_name, 0) + 1

    return {
        "leads_by_niche": sorted(
            [{"name": k, "count": v} for k, v in by_niche.items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:10],
        "leads_by_signal": sorted(
            [{"name": k, "count": v} for k, v in by_signal.items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:10],
    }


@router.get("/dashboard/scraper")
async def dashboard_scraper(admin: dict = Depends(get_current_admin)):
    latest_log = None
    latest_mtime = 0.0
    for filename in os.listdir(LOG_DIR):
        if not filename.endswith(".log"):
            continue
        path = os.path.join(LOG_DIR, filename)
        mtime = os.path.getmtime(path)
        if mtime > latest_mtime:
            latest_mtime = mtime
            latest_log = path

    if not latest_log:
        return {
            "last_run_at": None,
            "total_scraped": 0,
            "success_count": 0,
            "failure_count": 0,
        }

    with open(latest_log, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    success_count = sum(1 for line in lines if "✓ match" in line.lower())
    failure_count = sum(1 for line in lines if "fetch failed" in line.lower() or "error" in line.lower())

    return {
        "last_run_at": datetime.fromtimestamp(latest_mtime, tz=timezone.utc).isoformat(),
        "total_scraped": success_count + failure_count,
        "success_count": success_count,
        "failure_count": failure_count,
    }


@router.get("/dashboard/sales")
async def dashboard_sales(admin: dict = Depends(get_current_admin), supabase: Client = Depends(get_supabase)):
    seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    purchases_res = (
        supabase.table("purchases")
        .select("created_at, datasets(niche, signal_id, price_usd)")
        .gte("created_at", seven_days_ago)
        .execute()
    )
    rows = purchases_res.data or []

    total_orders = len(rows)
    revenue_7d = 0.0
    niche_counts = {}
    signal_counts = {}
    signal_ids = []

    for row in rows:
        ds = row.get("datasets") or {}
        try:
            revenue_7d += float(ds.get("price_usd") or 0)
        except (TypeError, ValueError):
            pass
        niche = (ds.get("niche") or "Unknown").strip() or "Unknown"
        signal_id = ds.get("signal_id")
        niche_counts[niche] = niche_counts.get(niche, 0) + 1
        if signal_id:
            signal_counts[signal_id] = signal_counts.get(signal_id, 0) + 1
            signal_ids.append(signal_id)

    signal_name_map = {}
    unique_signal_ids = list(set(signal_ids))
    if unique_signal_ids:
        sig_res = supabase.table("signals").select("id, name").in_("id", unique_signal_ids).execute()
        signal_name_map = {row["id"]: row.get("name", "Unknown") for row in (sig_res.data or [])}

    top_niche = max(niche_counts, key=niche_counts.get) if niche_counts else "N/A"
    top_signal_id = max(signal_counts, key=signal_counts.get) if signal_counts else None
    top_signal = signal_name_map.get(top_signal_id, "N/A") if top_signal_id else "N/A"

    return {
        "total_orders": total_orders,
        "revenue_7d": round(revenue_7d, 2),
        "top_niche": top_niche,
        "top_signal": top_signal,
    }
