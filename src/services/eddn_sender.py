"""
EDDN (Elite Dangerous Data Network) sender module.
Strictly filters fields to match EDDN journal/1 schema.
"""

import asyncio
import logging
import re
import json
from copy import deepcopy
from typing import Any, Optional

try:
    import aiohttp
except ImportError:
    aiohttp = None

EDDN_SCHEMA_REF = "https://eddn.edcd.io/schemas/journal/1"
EDDN_UPLOAD_URL = "https://eddn.edcd.io:4430/upload/"
EDDN_TIMEOUT_SEC = 8
SOFTWARE_NAME = "skybioml.net"
SOFTWARE_VERSION = "1.4.0"

# БЕЛЫЙ СПИСОК ПОЛЕЙ (согласно схеме journal/1)
# Любое поле НЕ из этого списка вызовет HTTP 400
ALLOWED_FIELDS = {
    "FSDJump": {
        "timestamp", "event", "StarSystem", "SystemAddress", "StarPos", "SystemAllegiance",
        "SystemEconomy", "SystemSecondEconomy", "SystemGovernment", "SystemSecurity",
        "Population", "Body", "BodyID", "BodyType", "Factions", "SystemFaction", "SystemState",
        "horizons", "odyssey"
    },
    "Scan": {
        "timestamp", "event", "BodyName", "BodyID", "Parents", "StarSystem", "SystemAddress",
        "DistanceFromArrivalLS", "StarType", "Subclass", "StellarMass", "Radius", "AbsoluteMagnitude",
        "Age_MY", "SurfaceTemperature", "Luminosity", "SemiMajorAxis", "Eccentricity",
        "OrbitalInclination", "Periapsis", "OrbitalPeriod", "AscendingNode", "MeanAnomaly",
        "RotationPeriod", "AxialTilt", "Rings", "WasDiscovered", "WasMapped", "WasFootfalled",
        "PlanetClass", "Atmosphere", "AtmosphereType", "AtmosphereComposition", "Volcanism",
        "MassEM", "SurfaceGravity", "SurfacePressure", "Composition", "TerraformState", "TidalLock",
        "horizons", "odyssey"
    },
    "FSSDiscoveryScan": {
        "timestamp", "event", "BodyCount", "NonBodyCount", "SystemName", "SystemAddress"
    },
    "SAASignalsFound": {
        "timestamp", "event", "BodyName", "SystemAddress", "BodyID", "Signals", "Genuses"
    }
}

def _filter_fields_by_schema(event_data: dict) -> dict:
    """Оставляет в сообщении только те поля, которые разрешены схемой EDDN."""
    event_type = event_data.get("event")
    allowed = ALLOWED_FIELDS.get(event_type)
    
    if not allowed:
        return event_data # Если события нет в списке, шлем как есть (на свой страх и риск)

    return {k: v for k, v in event_data.items() if k in allowed}

def _strip_localised_keys(obj: Any) -> Any:
    if not isinstance(obj, dict): return obj
    return {k: _strip_localised_keys(v) for k, v in obj.items() if not (isinstance(k, str) and k.endswith("_Localised"))}

def _timestamp_iso8601_no_ms(ts: str) -> str:
    if not ts or not isinstance(ts, str): return ts
    m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d+)?(Z?)$", ts.strip())
    if m: return m.group(1) + (m.group(2) or "Z")
    return ts

def _normalize_flags(message: dict) -> dict:
    out = dict(message)
    for key in ("Horizons", "horizons", "Odyssey", "odyssey"):
        if key in out:
            val = out.pop(key)
            out[key.lower()] = bool(val)
    return out

def build_eddn_payload(event_data: dict, game_state: Optional[dict] = None) -> dict:
    game_state = game_state or {}
    uploader_id = game_state.get("commander") or "Unknown_Commander"
    gameversion = game_state.get("gameversion") or "4.3.0.1"
    gamebuild = game_state.get("gamebuild") or "r322188/r0 "

    # 1. Сначала чистим локализацию и нормализуем флаги
    msg = _strip_localised_keys(deepcopy(event_data))
    msg = _normalize_flags(msg)
    
    # 2. СТРОГАЯ ФИЛЬТРАЦИЯ ПО БЕЛОМУ СПИСКУ
    msg = _filter_fields_by_schema(msg)

    if "timestamp" in msg:
        msg["timestamp"] = _timestamp_iso8601_no_ms(msg["timestamp"])

    return {
        "$schemaRef": EDDN_SCHEMA_REF,
        "header": {
            "uploaderID": uploader_id,
            "softwareName": SOFTWARE_NAME,
            "softwareVersion": SOFTWARE_VERSION,
            "gameversion": gameversion,
            "gamebuild": gamebuild,
        },
        "message": msg,
    }

async def send_to_eddn(event_data: dict, game_state: Optional[dict] = None, timeout: float = EDDN_TIMEOUT_SEC) -> bool:
    if aiohttp is None: return False
    payload = build_eddn_payload(event_data, game_state)
    
    # Оставляем короткий дебаг, чтобы видеть только ивент и статус
    logging.info(f"🚀 EDDN: Sending {event_data.get('event')}...")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(EDDN_UPLOAD_URL, json=payload, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status == 200:
                    logging.info("✅ EDDN: Upload Success")
                    return True
                
                err_text = await resp.text()
                logging.warning(f"❌ EDDN: HTTP {resp.status} - {err_text}")
                return False
    except Exception as e:
        logging.warning(f"⚠️ EDDN: Error {e}")
        return False