# app.py
import asyncio, os, xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Dict
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
import httpx

# === Configuration from environment variables ===
CTA_URL = "http://lapi.transitchicago.com/api/1.0/ttarrivals.aspx"
BUS_URL = "https://www.ctabustracker.com/bustime/api/v3/getpredictions"
API_KEY_TRAIN = os.environ.get("CTA_API_KEY_TRAIN", "")
API_KEY_BUS = os.environ.get("CTA_API_KEY_BUS", "")
MAP_ID = os.environ.get("CTA_MAP_ID", "")      # e.g., 40380 (Clark/Lake)
STP_ID = os.environ.get("CTA_STP_ID", "")      # optional platform/direction
BUS_STP_ID = os.environ.get("CTA_BUS_STP_ID", "")   # bus stop id (stpid)
MAX_RESULTS = int(os.environ.get("CTA_MAX", "8"))
POLL_SECONDS = int(os.environ.get("CTA_POLL_SECONDS", "60"))

app = FastAPI(title="CTA Departures")

_cache: Dict[str, Any] = {"updated_at": None, "data": [], "error": None}
_bus_cache: Dict[str, Any] = {"updated_at": None, "data": [], "error": None}

def _parse_eta(elem: ET.Element) -> Dict[str, Any]:
    tz = ZoneInfo("America/Chicago")
    def txt(name: str) -> str:
        node = elem.find(name)
        return node.text.strip() if node is not None and node.text else ""
    prdt = datetime.strptime(txt("prdt"), "%Y%m%d %H:%M:%S").replace(tzinfo=tz)
    arrT = datetime.strptime(txt("arrT"), "%Y%m%d %H:%M:%S").replace(tzinfo=tz)
    minutes = max(int((arrT - prdt).total_seconds() // 60), 0)
    return {
        "station_name": txt("staNm"),
        "platform": txt("stpDe"),
        "route": txt("rt"),
        "dest_name": txt("destNm"),
        "predicted_at": prdt.isoformat(),
        "arrives_at": arrT.isoformat(),
        "minutes": minutes,
        "is_approaching": txt("isApp") == "1",
        "is_scheduled": txt("isSch") == "1",
        "is_delayed": txt("isDly") == "1",
    }

# --- Bus Tracker prediction parser ---
def _parse_bus_prd(prd: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a Bus Tracker prediction payload into a small dict we can show.
    Expects JSON from v3 getpredictions with format=json. See CTA docs.
    """
    # prdctdn is minutes until arrival as a string (e.g., "7" or "DUE")
    raw_mins = str(prd.get("prdctdn", "")).strip()
    if raw_mins.isdigit():
        minutes = int(raw_mins)
    else:
        minutes = 0 if raw_mins.upper() == "DUE" else None

    return {
        "stop_id": prd.get("stpid", ""),
        "stop_name": prd.get("stpnm", ""),
        "route": prd.get("rt", ""),
        "direction": prd.get("rtdir", ""),
        "dest_name": prd.get("des", ""),
        "vehicle_id": prd.get("vid", ""),
        "predicted_at": prd.get("tmstmp", ""),
        "arrives_at": prd.get("prdtm", ""),
        "minutes": minutes,
        "is_delayed": bool(prd.get("dly", False)),
        "dyn": prd.get("dyn", 0),  # dynamic action type (0 normal)
    }

async def poll_once():
    if not API_KEY_TRAIN:
        raise RuntimeError("Set CTA_API_KEY")
    params = {"key": API_KEY_TRAIN, "max": str(MAX_RESULTS)}
    if STP_ID:
        params["stpid"] = STP_ID
    elif MAP_ID:
        params["mapid"] = MAP_ID
    else:
        raise RuntimeError("Set CTA_MAP_ID or CTA_STP_ID")

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(CTA_URL, params=params)
        r.raise_for_status()
        root = ET.fromstring(r.text)  # <ctatt> root
        err_code = root.findtext("errCd")
        err_name = root.findtext("errNm")
        if err_code and err_code != "0":
            raise RuntimeError(f"CTA error {err_code}: {err_name}")
        etas = [_parse_eta(eta) for eta in root.findall("eta")]
        etas.sort(key=lambda e: (e["arrives_at"], e["route"]))
        _cache["updated_at"] = datetime.now(ZoneInfo("America/Chicago")).isoformat()
        _cache["data"] = etas
        _cache["error"] = None

# --- Bus Tracker polling ---
async def poll_bus_once():
    if not API_KEY_BUS:
        raise RuntimeError("Set CTA_API_KEY_BUS")
    if not BUS_STP_ID:
        raise RuntimeError("Set CTA_BUS_STP_ID (bus stop ID, stpid)")

    params = {
        "key": API_KEY_BUS,
        "stpid": BUS_STP_ID,
        "format": "json",
        # you can also pass rt= to constrain, and top= for max results
    }

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(BUS_URL, params=params)
        r.raise_for_status()
        j = r.json()
        if "bustime-response" not in j:
            raise RuntimeError("Unexpected Bus API response")
        resp = j["bustime-response"]
        if "error" in resp and resp["error"]:
            # error may be a list of objects with msg fields
            # normalize into string
            try:
                msg = "; ".join(e.get("msg", "") for e in resp["error"]) or "Bus API error"
            except Exception:
                msg = str(resp["error"])  # fallback
            raise RuntimeError(msg)

        prds = resp.get("prd", []) or []
        items = [_parse_bus_prd(p) for p in prds]
        items = [p for p in items if p.get("direction", "").lower() == "southbound"]
        # sort by arrives_at then route
        items.sort(key=lambda e: (e.get("arrives_at") or "", e.get("route") or ""))
        _bus_cache["updated_at"] = datetime.now(ZoneInfo("America/Chicago")).isoformat()
        _bus_cache["data"] = items
        _bus_cache["error"] = None

async def poll_forever():
    while True:
        try:
            await poll_once()
        except Exception as e:
            _cache["error"] = str(e)
        await asyncio.sleep(POLL_SECONDS)

# --- Bus background poller ---
async def poll_bus_forever():
    while True:
        try:
            await poll_bus_once()
        except Exception as e:
            _bus_cache["error"] = str(e)
        await asyncio.sleep(POLL_SECONDS)

@app.on_event("startup")
async def _startup():
    if not API_KEY_TRAIN:
        raise RuntimeError("CTA_API_KEY_TRAIN is required")
    # start background loop that polls every POLL_SECONDS
    asyncio.create_task(poll_forever())
    asyncio.create_task(poll_bus_forever())

@app.get("/api/departures")
def get_departures():
    if _cache["updated_at"] is None and _cache["error"]:
        raise HTTPException(status_code=502, detail=_cache["error"])
    return _cache


# --- Bus API endpoint ---
@app.get("/api/bus")
def get_bus_departures():
    if _bus_cache["updated_at"] is None and _bus_cache["error"]:
        raise HTTPException(status_code=502, detail=_bus_cache["error"])
    return _bus_cache

@app.get("/", response_class=HTMLResponse)
def home():
    html = """
    <!doctype html><html><head><meta charset="utf-8">
    <meta http-equiv="refresh" content="30">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>CTA Departures</title>
    <style>
      body{font-family:system-ui,Segoe UI,Roboto,Inter,Arial;margin:2rem}
      .row{display:flex;gap:1rem;align-items:center;padding:.5rem 0;border-bottom:1px solid #eee}
      .badge{font-weight:700;padding:.2rem .5rem;border-radius:.5rem;border:1px solid #ccc}
      .muted{color:#555}
      .err{color:#b00020}
    </style></head><body>
      <h1>CTA Departures</h1>
      <p class="muted"></p>
      <div style="display:flex; gap:3rem; flex-wrap:wrap">
        <section>
          <h2>🚇 - Montrose</h2>
          <div id="status" class="muted"></div>
          <div id="list"></div>
        </section>
        <section>
          <h2>🚌 - Ashland & Montrose</h2>
          <div id="bus-status" class="muted"></div>
          <div id="bus-list"></div>
        </section>
      </div>
      <script>
        async function loadTrains(){
          const r = await fetch('/api/departures'); const j = await r.json();
          const s = document.getElementById('status');
          if(j.error){ s.innerHTML = "<span class='err'>"+j.error+"</span>"; }
          else{ s.textContent = "Updated: "+new Date(j.updated_at).toLocaleString(); }
          const list = document.getElementById('list'); list.innerHTML="";
          j.data.forEach(x=>{
            const d = document.createElement('div'); d.className="row";
            d.innerHTML = `
              <span class="badge">${x.route}</span>
              <strong>${x.minutes} min${x.minutes==1?"":"s"}</strong>
              <span class="muted">to ${x.dest_name}</span>
              <span class="muted">(${x.platform})</span>
              ${x.is_scheduled ? "<span class='badge'>SCHEDULED</span>":""}
              ${x.is_delayed ? "<span class='badge'>DELAYED</span>":""}
              ${x.is_approaching ? "<span class='badge'>APPROACHING</span>":""}
            `;
            list.appendChild(d);
          });
        }

        async function loadBuses(){
          const r = await fetch('/api/bus'); const j = await r.json();
          const s = document.getElementById('bus-status');
          if(j.error){ s.innerHTML = "<span class='err'>"+j.error+"</span>"; }
          else{ s.textContent = "Updated: "+new Date(j.updated_at).toLocaleString(); }
          const list = document.getElementById('bus-list'); list.innerHTML="";
          j.data.forEach(x=>{
            const d = document.createElement('div'); d.className="row";
            const mins = (x.minutes === null || x.minutes === undefined) ? '' : `${x.minutes} min${x.minutes==1?"":"s"}`;
            d.innerHTML = `
              <span class="badge">${x.route}</span>
              <strong>${mins || 'DUE'}</strong>
              <span class="muted">to ${x.dest_name}</span>
              <span class="muted">(${x.stop_name})</span>
              ${x.is_delayed ? "<span class='badge'>DELAYED</span>":""}
            `;
            list.appendChild(d);
          });
        }

        function loadAll(){ loadTrains(); loadBuses(); }
        loadAll(); setInterval(loadAll, 30000);
      </script>
    </body></html>
    """
    return HTMLResponse(html)