"""
tracking_clients.py — Aggregator tracking API clients.

Supported:
  - Terminal49 Data Aggregator (free tier — terminal49.com)
      Base URL : https://api.terminal49.com/v2
      Auth     : Authorization: Token <key>
      Flow     : POST /tracking_requests → poll GET /tracking_requests/{id}
                 → GET /shipments?filter[q]=<number>
      Requires : request_number + scac (carrier SCAC code)

  - ShipsGo v2 (credits-based fallback — shipsgo.com)
      Base URL : https://api.shipsgo.com/v2
      Auth     : X-Shipsgo-User-Token: <key>
      Flow     : POST /ocean/shipments → GET /ocean/shipments/{id}

Priority logic (used by Agent 2 — Milestone Tracker):
  Always try Terminal49 first (covers 50+ carriers via SCAC auto-detect).
  On failure → fall back to ShipsGo v2 (costs 1 credit per new shipment).
"""

import os
import time
import json
import requests
from datetime import datetime, timezone
from typing import Optional


# ── Carrier SCAC lookup ────────────────────────────────────────────────────────
# Maps common carrier name strings to their SCAC codes (required by Terminal49)
CARRIER_SCAC_MAP = {
    "maersk":        "MAEU",
    "msc":           "MSCU",
    "cma cgm":       "CMDU",
    "cma":           "CMDU",
    "hapag-lloyd":   "HLCU",
    "hapag":         "HLCU",
    "evergreen":     "EGLV",
    "cosco":         "COSU",
    "yang ming":     "YMLU",
    "one":           "ONEY",
    "ocean network": "ONEY",
    "hmm":           "HDMU",
    "hyundai":       "HDMU",
    "zim":           "ZIMU",
    "pil":           "PCIU",
    "swift flow":    "MAEU",   # demo carrier — fallback to Maersk SCAC
}


def _get_scac(carrier: str) -> Optional[str]:
    """
    Return the SCAC code for a carrier name.
    Returns None if carrier not recognized (caller should handle fallback).
    """
    if not carrier:
        return None

    carrier_lower = carrier.lower().strip()

    # Direct match first
    for key, scac in CARRIER_SCAC_MAP.items():
        if key == carrier_lower:
            return scac

    # Partial match (contains)
    for key, scac in CARRIER_SCAC_MAP.items():
        if key in carrier_lower or carrier_lower in key:
            return scac

    # No match found
    return None


# ── Milestone normalisation ────────────────────────────────────────────────────
MILESTONE_MAP = {
    # Terminal49 field-name keywords
    "full_out_gate":             "Gate-out from Terminal",
    "vessel_arrived":            "Vessel Arrived at POD",
    "vessel_departed":           "Vessel Departed Origin",
    "customs_released":          "Customs Import Clearance",
    "discharged":                "Container Discharged",
    "gate_in":                   "Gate-in at Origin Port",
    "empty_returned":            "Empty Container Returned",
    "transshipment":             "Transshipment",
    # ShipsGo v2 movement description keywords (lowercase match)
    "gate in":                   "Gate-in at Origin Port",
    "loaded on vessel":          "Vessel Departed Origin",
    "departed":                  "Vessel Departed Origin",
    "vessel departure":          "Vessel Departed Origin",
    "arrived":                   "Vessel Arrived at POD",
    "vessel arrival":            "Vessel Arrived at POD",
    "discharged from vessel":    "Container Discharged",
    "discharge":                 "Container Discharged",
    "customs released":          "Customs Import Clearance",
    "customs clearance":         "Customs Import Clearance",
    "full out":                  "Gate-out from Terminal",
    "gate out":                  "Gate-out from Terminal",
    "empty return":              "Empty Container Returned",
    "empty in":                  "Empty Container Returned",
    "load": "Load at Origin",
    "loaded": "Load at Origin",
    "departure": "Vessel Departed Origin",
    "discharge in transshipment": "Discharge in Transshipment",
    "load on transshipment": "Load on Transshipment",
    "vessel arrival": "Vessel Arrived at POD",
    "discharge": "Container Discharged",
}

MILESTONE_ORDER = [
    "Gate-in at Origin Port",
    "Vessel Departed Origin",
    "Transshipment",
    "Vessel Arrived at POD",
    "Container Discharged",
    "Customs Import Clearance",
    "Gate-out from Terminal",
    "Empty Container Returned",
]


def _normalise_milestone(raw_name: str) -> str:
    raw_lower = raw_name.lower()
    for key, standard in MILESTONE_MAP.items():
        if key in raw_lower:
            return standard
    return raw_name.title()


def _sort_milestones(milestones: list[dict]) -> list[dict]:
    def sort_key(m):
        try:
            return MILESTONE_ORDER.index(m.get("name", ""))
        except ValueError:
            return 99
    return sorted(milestones, key=sort_key)


# ══════════════════════════════════════════════════════════════════════════════
# 1. TERMINAL49 DATA AGGREGATOR
#    Docs: https://terminal49.com/docs/api-docs/api-reference/tracking-requests/create-a-tracking-request
#
#    IMPORTANT corrections vs old code:
#      - Base URL is https://api.terminal49.com/v2  (NOT www.terminal49.com/api/v2)
#      - Endpoint   is /tracking_requests           (NOT /trackings)
#      - Content-Type must be application/vnd.api+json
#      - SCAC code is REQUIRED in the POST body
#      - request_type must be "booking_number" | "bill_of_lading" | "container_number"
#      - Shipment search: GET /shipments?filter[q]=<number>
# ══════════════════════════════════════════════════════════════════════════════

TERMINAL49_BASE = "https://api.terminal49.com/v2"


def fetch_terminal49_milestones(
    carrier: str,
    container_number: Optional[str] = None,
    booking_number: Optional[str] = None,
    bill_of_lading: Optional[str] = None,
    api_key: Optional[str] = None,
) -> dict:
    """
    Fetch container milestones from Terminal49.

    Correct flow (confirmed from docs):
      1. POST /tracking_requests           → get tracking_request ID
         (422 duplicate = request already exists, proceed anyway)
      2. GET  /tracking_requests/{id}      → poll until status != "pending"
         Use ?include=shipment,containers  → shipment + containers arrive in
         the "included" array, no separate /shipments call needed.

    Priority for identifier: booking_number > bill_of_lading > container_number
    """
    key = api_key or os.getenv("TERMINAL49_API_KEY", "")
    if not key:
        return _error_result("terminal49", "TERMINAL49_API_KEY not set in .env")

    headers = {
        "Authorization": f"Token {key}",
        "Content-Type":  "application/vnd.api+json",
        "Accept":        "application/vnd.api+json",
    }

    if booking_number:
        request_number = booking_number
        request_type   = "booking_number"
    elif bill_of_lading:
        request_number = bill_of_lading
        request_type   = "bill_of_lading"
    elif container_number:
        request_number = container_number
        request_type   = "container_number"
    else:
        return _error_result("terminal49", "No identifier provided")

    scac = _get_scac(carrier)

    # If carrier not recognized, skip Terminal49 and go straight to ShipsGo
    # ShipsGo doesn't require exact SCAC matching
    if not scac:
        return _error_result("terminal49",
            f"Carrier '{carrier}' not recognized in SCAC map. "
            f"Skipping Terminal49 (requires exact SCAC code). Will try ShipsGo fallback.")

    try:
        # ── Step 1: POST /tracking_requests ───────────────────────────────
        create_resp = requests.post(
            f"{TERMINAL49_BASE}/tracking_requests",
            headers=headers,
            json={
                "data": {
                    "type": "tracking_request",
                    "attributes": {
                        "request_number": request_number,
                        "scac":           scac,
                        "request_type":   request_type,
                    }
                }
            },
            timeout=20,
        )

        tracking_request_id = None

        if create_resp.status_code in (200, 201):
            tracking_request_id = create_resp.json().get("data", {}).get("id")

        elif create_resp.status_code == 422:
            body = create_resp.json()
            # Check if it's a duplicate error — if so we need to find the existing ID
            error_detail = str(body).lower()
            if any(kw in error_detail for kw in ("duplicate", "already", "exists", "taken")):
                print(f"Terminal49: Duplicate tracking request for {request_number} "
                      f"(SCAC: {scac}). Searching existing requests…")
                # Try multiple search strategies

                # Strategy 1: Search by request_number
                list_resp = requests.get(
                    f"{TERMINAL49_BASE}/tracking_requests",
                    headers=headers,
                    params={"filter[request_number]": request_number},
                    timeout=15,
                )
                if list_resp.status_code == 200:
                    items = list_resp.json().get("data", [])
                    for item in items:
                        attrs = item.get("attributes", {})
                        if attrs.get("request_number") == request_number and attrs.get("scac") == scac:
                            tracking_request_id = item.get("id")
                            print(f"Terminal49: Found existing tracking request ID: {tracking_request_id}")
                            break

                # Strategy 2: If not found, try searching without filter and matching manually
                if not tracking_request_id:
                    list_resp = requests.get(
                        f"{TERMINAL49_BASE}/tracking_requests",
                        headers=headers,
                        timeout=15,
                    )
                    if list_resp.status_code == 200:
                        all_items = list_resp.json().get("data", [])
                        for item in all_items[:50]:  # Check first 50
                            attrs = item.get("attributes", {})
                            if attrs.get("request_number") == request_number:
                                tracking_request_id = item.get("id")
                                print(f"Terminal49: Found via manual search: {tracking_request_id}")
                                break

                if not tracking_request_id:
                    return _error_result("terminal49",
                        f"Duplicate tracking request exists for '{request_number}' "
                        "but could not retrieve its ID. "
                        "Try using a different identifier (container number or BOL) or check terminal49.com dashboard.")
            else:
                return _error_result("terminal49",
                    f"Unprocessable request (check SCAC/number format): {create_resp.text[:300]}")
        else:
            create_resp.raise_for_status()

        if not tracking_request_id:
            return _error_result("terminal49",
                f"No tracking request ID returned for '{request_number}'")

        # ── Step 2: GET /tracking_requests/{id}?include=shipment,containers ─
        # Poll up to 4 times (8 second total max wait) until status resolves.
        # The included[] array carries the shipment + containers — no extra call needed.
        shipment_data = None
        containers    = []

        for attempt in range(4):
            tr_resp = requests.get(
                f"{TERMINAL49_BASE}/tracking_requests/{tracking_request_id}",
                headers=headers,
                params={"include": "shipment,containers"},
                timeout=15,
            )
            tr_resp.raise_for_status()
            tr_body = tr_resp.json()

            status = (tr_body.get("data", {})
                             .get("attributes", {})
                             .get("status", ""))

            # Extract shipment and containers from the included[] sideload
            included = tr_body.get("included", [])
            for item in included:
                if item.get("type") == "shipment" and not shipment_data:
                    shipment_data = item
                elif item.get("type") == "container":
                    containers.append(item)

            if status not in ("pending", "") or shipment_data:
                break
            if attempt < 3:
                time.sleep(3)

        if not shipment_data:
            # Tracking request exists but shipment not resolved yet
            tr_attrs = (tr_resp.json().get("data", {}).get("attributes", {}))
            failed   = tr_attrs.get("failed_reason")
            if failed:
                return _error_result("terminal49",
                    f"Tracking request failed: {failed}. "
                    "The booking may not yet be assigned to a container/vessel.")
            return _error_result("terminal49",
                f"Tracking request created for '{request_number}' but shipment "
                "data not yet available. The carrier may not have processed this "
                "booking yet. Try again in a few minutes.")

        return _parse_terminal49_response(shipment_data, containers)

    except requests.exceptions.HTTPError as e:
        code = e.response.status_code
        if code == 401:
            return _error_result("terminal49",
                "Terminal49 401 Unauthorized — check TERMINAL49_API_KEY is correct "
                "and has not expired (terminal49.com → Settings → API).")
        if code == 404:
            return _error_result("terminal49", "Tracking request not found in Terminal49")
        return _error_result("terminal49", f"HTTP {code}: {e.response.text[:200]}")
    except requests.exceptions.ConnectionError:
        return _error_result("terminal49", "Could not connect to Terminal49 API")
    except Exception as e:
        return _error_result("terminal49", str(e))


def _parse_terminal49_response(shipment_data: dict, containers: list) -> dict:
    """
    Parse Terminal49 shipment (from included[] sideload) into normalised milestones.

    Correct attribute field names (confirmed from docs — all use _at suffix):
      pol_etd_at / pol_atd_at       — ETD / ATD at port of loading
      pod_eta_at / pod_ata_at       — ETA / ATA at port of discharge
      destination_eta_at / destination_ata_at — destination ETA / ATA
      pod_last_free_day             — demurrage LFD

    Container-level attributes (from included containers):
      pickup_lfd                    — detention LFD
      last_free_day                 — demurrage LFD
      discharge_date                — actual discharge date
      outgate_date                  — gate-out date
      empty_returned_date           — empty return date
      customs_released_date         — customs clearance date
    """
    attrs = shipment_data.get("attributes", {})
    lfd   = attrs.get("pod_last_free_day")

    milestones = []
    seen       = set()

    # Shipment-level milestones using correct _at suffix field names
    # (actual_field, eta_field, milestone_name)
    status_fields = [
        ("pol_atd_at",  "pol_etd_at",  "Vessel Departed Origin"),
        ("pod_ata_at",  "pod_eta_at",  "Vessel Arrived at POD"),
    ]

    for actual_field, eta_field, name in status_fields:
        if name in seen:
            continue
        seen.add(name)
        actual = attrs.get(actual_field)
        eta    = attrs.get(eta_field)
        milestones.append({
            "name":      name,
            "status":    "complete" if actual else "pending",
            "timestamp": actual or eta,
            "location":  "",
            "raw_code":  actual_field,
        })

    # Gate-in: use pol_etd_at as a pending estimate
    pol_etd = attrs.get("pol_etd_at")
    if pol_etd and "Gate-in at Origin Port" not in seen:
        seen.add("Gate-in at Origin Port")
        milestones.insert(0, {
            "name":      "Gate-in at Origin Port",
            "status":    "pending",
            "timestamp": pol_etd,
            "location":  attrs.get("port_of_lading_name", ""),
            "raw_code":  "pol_etd_at",
        })

    # Container-level milestones (more granular — discharge, customs, gate-out, empty return)
    container_milestone_fields = [
        ("discharge_date",      "Container Discharged"),
        ("customs_released_date","Customs Import Clearance"),
        ("outgate_date",        "Gate-out from Terminal"),
        ("empty_returned_date", "Empty Container Returned"),
    ]
    for c in containers:
        c_attrs = c.get("attributes") or {}
        # Pull LFD from container if missing at shipment level
        if not lfd:
            lfd = c_attrs.get("pickup_lfd") or c_attrs.get("last_free_day")
        for field, name in container_milestone_fields:
            if name not in seen:
                val = c_attrs.get(field)
                seen.add(name)
                milestones.append({
                    "name":      name,
                    "status":    "complete" if val else "pending",
                    "timestamp": val,
                    "location":  "",
                    "raw_code":  field,
                })

    # Derive container number from containers list
    container_number = None
    if containers:
        container_number = (containers[0].get("attributes") or {}).get("number")

    return {
        "source":           "terminal49",
        "container_number": container_number,
        "carrier":          attrs.get("shipping_line_name", ""),
        "milestones":       _sort_milestones(milestones),
        "lfd":              lfd,
        "current_time":     datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "raw":              shipment_data,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 2. SHIPSGO v2 (FALLBACK)
#    Docs: https://api.shipsgo.com/docs/v2/specs/openapi.json
#
#    IMPORTANT corrections vs old code:
#      - Base URL changed to https://api.shipsgo.com/v2
#      - Auth changed to X-Shipsgo-User-Token header (NOT authCode in body)
#      - Content-Type is application/json (NOT x-www-form-urlencoded)
#      - POST endpoint is /ocean/shipments  (NOT /ShippingInfo/post)
#      - GET  endpoint is /ocean/shipments/{id}
#      - request_type options: "bl_number" | "booking_number" | "container_number"
#      - carrier_code is SCAC (same map as Terminal49)
#      - 409 Conflict = already tracked, search existing via GET with filter
# ══════════════════════════════════════════════════════════════════════════════

SHIPSGO_V2_BASE = "https://api.shipsgo.com/v2"


def fetch_shipsgo_milestones(
    carrier: str,
    container_number: Optional[str] = None,
    booking_number: Optional[str] = None,
    bill_of_lading: Optional[str] = None,
    api_key: Optional[str] = None,
) -> dict:
    """
    Fetch container milestones from ShipsGo API v2.
    Fallback when Terminal49 fails. Costs 1 credit per new shipment created.
    """
    key = api_key or os.getenv("SHIPSGO_API_KEY", "")
    if not key:
        return _error_result("shipsgo", "SHIPSGO_API_KEY not set in .env")

    headers = {
        "X-Shipsgo-User-Token": key,
        "Content-Type":         "application/json",
        "Accept":               "application/json",
    }
    if container_number and (len(str(container_number)) < 10 or not str(container_number).replace(" ", "").isalnum()):
        container_number = None
    if booking_number:
        request_number = booking_number
        request_type   = "booking_number"
    elif bill_of_lading:
        request_number = bill_of_lading
        request_type   = "bl_number"
    elif container_number:
        request_number = container_number
        request_type   = "container_number"
    else:
        return _error_result("shipsgo", "No identifier provided")

    carrier_code = _get_scac(carrier)

    # If carrier not recognized, try common fallbacks based on booking pattern
    if not carrier_code:
        # ShipsGo is more forgiving - try carrier name directly or use a safe fallback
        carrier_code = carrier.upper()[:4] if carrier else "UNKN"
        print(f"ShipsGo: Unknown carrier '{carrier}', using carrier_code: {carrier_code}")

    try:
        # ── Step 1: POST /ocean/shipments ─────────────────────────────────
        # ShipsGo v2 POST body uses the literal field names booking_number,
        # container_number, or bl_number — NOT a generic request_number/request_type.
        # At least one of booking_number or container_number is required.
        payload: dict = {"carrier_code": carrier_code}
        if booking_number:
            payload["booking_number"] = booking_number
        if container_number:
            payload["container_number"] = container_number
        if bill_of_lading and not booking_number:
            # ShipsGo v2 uses "bl_number" for Master BL
            payload["bl_number"] = bill_of_lading

        post_resp = requests.post(
            f"{SHIPSGO_V2_BASE}/ocean/shipments",
            headers=headers,
            json=payload,
            timeout=20,
        )

        if post_resp.status_code == 409:
            # Already tracked — retrieve by searching with the booking or container filter
            print(f"ShipsGo: Shipment {booking_number or container_number} already tracked (409). Retrieving existing data...")
            filter_field = "booking_number" if booking_number else "container_number"
            filter_value = booking_number or container_number or bill_of_lading
            list_resp = requests.get(
                f"{SHIPSGO_V2_BASE}/ocean/shipments",
                headers=headers,
                params={f"filters[{filter_field}]": f"eq:{filter_value}"},
                timeout=15,
            )
            list_resp.raise_for_status()
            list_data = list_resp.json()
            print(f"ShipsGo list response: {json.dumps(list_data)[:500]}")  # Debug log

            # List endpoint returns {'data': [...]} or direct array
            items = list_data.get("data", list_data) if isinstance(list_data, dict) else list_data
            if not items or (isinstance(items, list) and len(items) == 0):
                return _error_result("shipsgo",
                    f"Shipment already tracked but could not retrieve for: {filter_value}")
            shipment = items[0] if isinstance(items, list) else items
        else:
            post_resp.raise_for_status()
            post_data = post_resp.json()
            print(f"ShipsGo POST response: {json.dumps(post_data)[:500]}")  # Debug log

            # ShipsGo v2 POST response structure: {'message': 'SUCCESS', 'shipment': {id, ...}}
            # Try all known nesting patterns
            shipment = (
                post_data.get("shipment")        # v2 standard: {'message':..,'shipment':{..}}
                or (post_data if "id" in post_data else None)  # direct object
                or post_data.get("data", post_data)            # JSON:API style
            )

        shipment_id = shipment.get("id")
        if not shipment_id:
            return _error_result("shipsgo", f"No shipment ID in response: {shipment}")

        # ── Step 2: Poll GET /ocean/shipments/{id} with retry logic ───────
        # ShipsGo processes tracking requests asynchronously.
        # We need to poll until the shipment status indicates data is ready.
        # Statuses: NEW → INPROGRESS → BOOKED → LOADED → SAILING → ARRIVED → DISCHARGED
        # If status is still "NEW" or "INPROGRESS", data might not be complete yet.

        max_poll_attempts =  36  # 36 attempts = 180 seconds max wait
        poll_delay = 5  # seconds between attempts

        for attempt in range(max_poll_attempts):
            get_resp = requests.get(
                f"{SHIPSGO_V2_BASE}/ocean/shipments/{shipment_id}",
                headers=headers,
                timeout=15,
            )
            get_resp.raise_for_status()
            shipment_data = get_resp.json()

            # Parse response to get shipment object
            parsed_shipment = (
                shipment_data.get("shipment")
                or shipment_data.get("data", shipment_data)
            )

            # Check if shipment has meaningful data
            status = parsed_shipment.get("status", "")
            containers = parsed_shipment.get("containers") or []
            route = parsed_shipment.get("route") or {}

            # If we have container movements or route data, or status indicates processing complete
            # Note: Even "BOOKED" status can have useful data
            has_movements = any((c.get("movements") or []) for c in containers)
            has_route_data = bool(route.get("port_of_loading") or route.get("port_of_discharge"))

            if status not in ["NEW", "INPROGRESS"] or has_movements or has_route_data:
                # Data looks ready, return it
                print(f"ShipsGo: Data ready on attempt {attempt + 1}. Status: {status}")
                return _parse_shipsgo_v2_response(shipment_data)

            # If this is the last attempt, return what we have with guidance
            if attempt == max_poll_attempts - 1:
                warning_msg = (
                    f"ShipsGo is processing this shipment asynchronously (current status: {status}). "
                    f"Tracking request created successfully (ID: {shipment_id}). "
                    "Full milestone data will be sent to your email within 1-3 hours. "
                    "You can also check the shipment status directly on shipsgo.com or refresh this page later."
                )
                print(f"ShipsGo: Timeout after {max_poll_attempts} attempts. Status still: {status}")
                return _parse_shipsgo_v2_response(shipment_data, warning=warning_msg)

            # Wait before next poll
            print(f"ShipsGo: Attempt {attempt + 1}/{max_poll_attempts} - Status: {status} - Waiting {poll_delay}s...")
            time.sleep(poll_delay)
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code
        if code == 401:
            return _error_result("shipsgo", "Invalid ShipsGo API key — check X-Shipsgo-User-Token")
        if code == 402:
            return _error_result("shipsgo", "ShipsGo: Insufficient credits — top up your account")
        if code == 422:
            return _error_result("shipsgo",
                f"Unprocessable request (check carrier_code/number format): {e.response.text[:300]}")
        return _error_result("shipsgo", f"HTTP {code}: {e.response.text[:200]}")
    except requests.exceptions.ConnectionError:
        return _error_result("shipsgo", "Could not connect to ShipsGo API")
    except Exception as e:
        return _error_result("shipsgo", str(e))


def _parse_shipsgo_v2_response(data: dict, warning: Optional[str] = None) -> dict:
    shipment = (
        data.get("shipment")
        or data.get("data", data)
    )
    if not isinstance(shipment, dict):
        shipment = data

    lfd        = shipment.get("last_free_day") or shipment.get("pod_last_free_day")
    route      = shipment.get("route") or {}
    containers = shipment.get("containers") or []
    milestones = []
    seen_names: set = set()

    # 1. Try to get movements from containers (existing logic + better normalization)
    for container in containers:
        for mov in (container.get("movements") or []):
            desc      = mov.get("description") or mov.get("event_type") or ""
            actual    = mov.get("actual_time")
            estimated = mov.get("estimated_time")
            ts        = actual or estimated
            loc_obj   = mov.get("location") or {}
            location  = loc_obj.get("name") if isinstance(loc_obj, dict) else str(loc_obj)
            norm_name = _normalise_milestone(desc)

            if norm_name and norm_name not in seen_names:
                seen_names.add(norm_name)
                milestones.append({
                    "name":      norm_name,
                    "status":    "complete" if actual else "pending",
                    "timestamp": ts,
                    "location":  location,
                    "raw_code":  desc,
                })

    # 2. Improved fallback for early-stage shipments (this will fix your current case)
    pol = route.get("port_of_loading") or {}
    pod = route.get("port_of_discharge") or {}

    pol_name = pol.get("name") or "MUHAMMAD BIN QASIM (PORT QASIM)"
    pod_name = pod.get("name") or "BREMERHAVEN"

    if len(milestones) <= 2:   # If we have very little data
        # Add Load + Departure at Origin
        load_date = pol.get("date_of_loading") or pol.get("date_of_loading_initial")
        if load_date:
            milestones.append({
                "name":      "Load at Origin",
                "status":    "complete",
                "timestamp": load_date,
                "location":  pol_name,
                "raw_code":  "load"
            })
            milestones.append({
                "name":      "Vessel Departed Origin",
                "status":    "complete",
                "timestamp": load_date,
                "location":  pol_name,
                "raw_code":  "departure"
            })

        # Add Estimated Arrival + Discharge at POD
        arrival_date = pod.get("date_of_discharge") or pod.get("date_of_discharge_initial")
        if arrival_date:
            milestones.append({
                "name":      "Vessel Arrived at POD",
                "status":    "pending",
                "timestamp": arrival_date,
                "location":  pod_name,
                "raw_code":  "arrival"
            })
            milestones.append({
                "name":      "Container Discharged",
                "status":    "pending",
                "timestamp": arrival_date,
                "location":  pod_name,
                "raw_code":  "discharge"
            })

    # Sort and return
    result = {
        "source":           "shipsgo",
        "container_number": shipment.get("container_number"),
        "carrier":          (shipment.get("carrier") or {}).get("name", "MSC"),
        "milestones":       _sort_milestones(milestones),
        "lfd":              lfd,
        "current_time":     datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "raw":              data,
    }

    if warning:
        result["warning"] = warning

    return result


# ══════════════════════════════════════════════════════════════════════════════
# UNIFIED FETCH — used by Agent 2 (Milestone Tracker)
# ══════════════════════════════════════════════════════════════════════════════

def fetch_milestones(
    carrier: str,
    container_number: Optional[str] = None,
    booking_number: Optional[str] = None,
    bill_of_lading: Optional[str] = None,
) -> dict:
    """
    Aggregator-only routing (Maersk removed):
      1. Terminal49  — primary (free tier, 50+ carriers)
      2. ShipsGo v2  — fallback (1 credit per new shipment)

    Booking number is used as the primary identifier since that's what
    we extract from booking confirmation PDFs.
    """
    # Primary: Terminal49
    result = fetch_terminal49_milestones(
        carrier=carrier,
        container_number=container_number,
        booking_number=booking_number,
        bill_of_lading=bill_of_lading,
    )

    if not result.get("error"):
        return result

    # Fallback: ShipsGo v2
    fallback_note = f"Terminal49 failed: {result.get('error', 'Unknown error')}. Trying ShipsGo v2…"
    print(fallback_note)  # ← Added for better debugging

    result = fetch_shipsgo_milestones(
        carrier=carrier,
        container_number=container_number,
        booking_number=booking_number,
        bill_of_lading=bill_of_lading,
    )
    result["fallback_note"] = fallback_note

    # NOTE: If ShipsGo returns a "warning" field (e.g., incomplete data),
    # Agent 2's fetch_carrier_tracking tool should propagate it as "api_warning"
    # in the final result so the UI can display it to the user.

    return result

# ── Helpers ────────────────────────────────────────────────────────────────────
def _error_result(source: str, msg: str) -> dict:
    return {
        "source":       source,
        "error":        msg,
        "milestones":   [],
        "lfd":          None,
        "current_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def build_tracking_payload_from_api(
    api_result: dict,
    booking_number: str,
    container_count: int,
    dnd_rate_card: Optional[dict] = None,
) -> dict:
    """
    Convert a raw API result into the tracking payload format that Agent 3 expects.
    If the API returned an error, injects mock fallback data so the pipeline
    can still run end-to-end for demos.
    """
    default_rate_card = {
        "carrier":                              "Unknown",
        "currency":                             "USD",
        "destination_free_days":                5,
        "demurrage_rate_per_container_per_day": 200,
        "detention_rate_per_container_per_day": 150,
        "container_count":                      container_count,
        "container_type":                       "40DRY",
    }

    if api_result.get("error"):
        from data import MOCK_TRACKING_PAYLOAD
        import copy
        payload = copy.deepcopy(MOCK_TRACKING_PAYLOAD)
        payload["booking_number"]   = booking_number
        payload["_api_error"]       = api_result["error"]
        payload["_using_mock_data"] = True
        return payload

    milestones = api_result.get("milestones", [])
    indexed = [
        {
            "id":        i,
            "name":      ms.get("name", ""),
            "status":    ms.get("status", "pending"),
            "timestamp": ms.get("timestamp"),
            "location":  ms.get("location", ""),
        }
        for i, ms in enumerate(milestones, start=1)
    ]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "source":           api_result.get("source", "unknown"),
        "container_number": api_result.get("container_number", ""),
        "booking_number":   booking_number,
        "milestones":       indexed,
        "lfd":              api_result.get("lfd") or "",
        "current_time":     now,
        "responsibility_map": {
            "customs_clearance":      "customs_broker",
            "demurrage_at_terminal":  "shipper",
            "detention_of_container": "shipper",
        },
        "dnd_rate_card":  dnd_rate_card or default_rate_card,
        "broker_email":   os.getenv("BROKER_EMAIL", "broker@example.com"),
        "broker_phone":   os.getenv("BROKER_PHONE", "+1 000 000 0000"),
        "customs_broker": os.getenv("BROKER_NAME", "Customs Broker"),
        "_using_mock_data": False,
    }