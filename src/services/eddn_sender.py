"""
EDDN (Elite Dangerous Data Network) sender module.
Strictly filters fields to match EDDN journal/1 schema.
"""

import json
import logging
import re
from copy import deepcopy
from typing import Any, Optional

import httpx

from config import SOFTWARE_VERSION

EDDN_SCHEMA_REF = "https://eddn.edcd.io/schemas/journal/1"
EDDN_SCHEMA_FSSBODYSIGNALS = "https://eddn.edcd.io/schemas/fssbodysignals/1"
EDDN_UPLOAD_URL = "https://eddn.edcd.io:4430/upload/"
EDDN_TIMEOUT_SEC = 8
SOFTWARE_NAME = "skybioml.net"

# БЕЛЫЙ СПИСОК ПОЛЕЙ (согласно схеме journal/1)
ALLOWED_FIELDS = {
    "FSDJump": {
        "timestamp",
        "event",
        "StarSystem",
        "SystemAddress",
        "StarPos",
        "SystemAllegiance",
        "SystemEconomy",
        "SystemSecondEconomy",
        "SystemGovernment",
        "SystemSecurity",
        "Population",
        "Body",
        "BodyID",
        "BodyType",
        "Factions",
        "SystemFaction",
        "SystemState",
        "Powers",
        "PowerplayState",
        "ControllingPower",
        "PowerplayStateControlProgress",
        "PowerplayStateReinforcement",
        "PowerplayStateUndermining",
        "horizons",
        "odyssey",
        "Taxi",
        "Multicrew",
    },
    "Scan": {
        "timestamp",
        "event",
        "BodyName",
        "BodyID",
        "Parents",
        "StarSystem",
        "SystemAddress",
        "DistanceFromArrivalLS",
        "StarType",
        "Subclass",
        "StellarMass",
        "Radius",
        "AbsoluteMagnitude",
        "Age_MY",
        "SurfaceTemperature",
        "Luminosity",
        "SemiMajorAxis",
        "Eccentricity",
        "OrbitalInclination",
        "Periapsis",
        "OrbitalPeriod",
        "AscendingNode",
        "MeanAnomaly",
        "RotationPeriod",
        "AxialTilt",
        "Rings",
        "WasDiscovered",
        "WasMapped",
        "WasFootfalled",
        "PlanetClass",
        "Atmosphere",
        "AtmosphereType",
        "AtmosphereComposition",
        "Volcanism",
        "MassEM",
        "SurfaceGravity",
        "SurfacePressure",
        "Landable",
        "Composition",
        "TerraformState",
        "TidalLock",
        "Materials",
        "ReserveLevel",
        "horizons",
        "odyssey",
        "StarPos",
    },
    "SAASignalsFound": {
        "timestamp",
        "event",
        "BodyName",
        "SystemAddress",
        "BodyID",
        "Signals",
        "Genuses",
        "StarSystem",
        "StarPos",
        "horizons",
        "odyssey",
    },
    "FSSBodySignals": {
        "timestamp",
        "event",
        "BodyID",
        "BodyName",
        "Signals",
        "StarSystem",
        "SystemAddress",
        "StarPos",
        "horizons",
        "odyssey",
    },
}


def _filter_fields_by_schema(event_data: dict) -> dict:
    event_type = event_data.get("event")
    allowed = ALLOWED_FIELDS.get(event_type)
    if not allowed:
        return event_data
    return {k: v for k, v in event_data.items() if k in allowed}


def _strip_localised_keys(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: _strip_localised_keys(v)
            for k, v in obj.items()
            if not (isinstance(k, str) and k.endswith("_Localised"))
        }
    if isinstance(obj, list):
        return [_strip_localised_keys(item) for item in obj]
    return obj


def _timestamp_iso8601_no_ms(ts: str) -> str:
    if not ts or not isinstance(ts, str):
        return ts
    m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d+)?(Z?)$", ts.strip())
    if m:
        return m.group(1) + (m.group(2) or "Z")
    return ts


def _normalize_flags(message: dict) -> dict:
    out = dict(message)
    for key in ("Horizons", "horizons", "Odyssey", "odyssey"):
        if key in out:
            val = out.pop(key)
            out[key.lower()] = bool(val)
    return out


# Keys allowed inside each Factions[] item (EDDN disallows MyReputation, SquadronFaction, etc.)
FACTIONS_ALLOWED_KEYS = frozenset(
    {
        "Name",
        "FactionState",
        "Government",
        "Influence",
        "Allegiance",
        "Happiness",
        "ActiveStates",
        "PendingStates",
        "RecoveringStates",
    }
)


def _clean_message_for_eddn(msg: dict) -> None:
    """Apply EDDN-specific cleaning to message in place. Call before _filter_fields_by_schema."""
    # --- Factions: keep only schema-allowed keys per item ---
    factions = msg.get("Factions")
    if isinstance(factions, list):
        msg["Factions"] = [
            {k: v for k, v in item.items() if k in FACTIONS_ALLOWED_KEYS}
            for item in factions
            if isinstance(item, dict)
        ]

    # --- SystemFaction: keep only Name ---
    system_faction = msg.get("SystemFaction")
    if isinstance(system_faction, dict) and "Name" in system_faction:
        msg["SystemFaction"] = {"Name": system_faction["Name"]}
    elif isinstance(system_faction, dict):
        msg["SystemFaction"] = {}

    # --- Composition: normalize 0..1 to 0..100 if all values in [0, 1] ---
    composition = msg.get("Composition")
    if isinstance(composition, dict):
        values = [v for v in composition.values() if isinstance(v, (int, float))]
        if values and all(0 <= v <= 1 for v in values):
            msg["Composition"] = {
                k: (v * 100 if isinstance(v, (int, float)) and 0 <= v <= 1 else v)
                for k, v in composition.items()
            }
    # Materials and AtmosphereComposition: leave as arrays; *_Localised already stripped at start
    # (no conversion to dict; schema expects array of objects)


def build_eddn_payload(event_data: dict, game_state: Optional[dict] = None) -> Optional[dict]:
    game_state = game_state or {}
    uploader_id = game_state.get("commander") or "Unknown_Commander"
    gameversion = game_state.get("gameversion") or "4.3.0.1"
    gamebuild = game_state.get("gamebuild") or "r322188/r0 "

    msg = _strip_localised_keys(deepcopy(event_data))
    msg = _normalize_flags(msg)

    # --- ИНЪЕКЦИЯ TECHNICAL TRUTH (DLC / Taxi / Multicrew из сессии) ---
    msg["horizons"] = game_state.get("is_horizons", False)
    msg["odyssey"] = game_state.get("is_odyssey", False)
    if msg.get("event") == "FSDJump":
        msg["Taxi"] = game_state.get("is_taxi", False)
        msg["Multicrew"] = game_state.get("is_multicrew", False)

    # --- ИНЪЕКЦИЯ КООРДИНАТ (SCAN + SAASignalsFound + FSSBodySignals) ---
    if msg.get("event") in ["SAASignalsFound", "Scan", "FSSBodySignals"]:
        if not msg.get("StarSystem") and game_state.get("star_system"):
            msg["StarSystem"] = game_state.get("star_system")
        if not msg.get("StarPos") and game_state.get("star_pos"):
            msg["StarPos"] = game_state.get("star_pos")

    # --- БЛОКИРОВКА ПРИ ОТСУТСТВИИ КООРДИНАТ ---
    # Если для события требуются координаты, но их все еще нет — НЕ ОТПРАВЛЯЕМ.
    # Это предотвращает HTTP 400 и спам битыми пакетами.
    if msg.get("event") in ["FSDJump", "SAASignalsFound", "Scan", "FSSBodySignals"]:
        if (
            not msg.get("StarPos")
            or not isinstance(msg.get("StarPos"), list)
            or len(msg.get("StarPos")) != 3
        ):
            # Для дебага можно раскомментировать
            # logging.warning(f"⚠️ EDDN: Missing StarPos for {msg.get('event')}. Skipping.")
            return None

    _clean_message_for_eddn(msg)
    msg = _filter_fields_by_schema(msg)

    if "timestamp" in msg:
        msg["timestamp"] = _timestamp_iso8601_no_ms(msg["timestamp"])

    schema_ref = (
        EDDN_SCHEMA_FSSBODYSIGNALS if msg.get("event") == "FSSBodySignals" else EDDN_SCHEMA_REF
    )
    return {
        "$schemaRef": schema_ref,
        "header": {
            "uploaderID": uploader_id,
            "softwareName": SOFTWARE_NAME,
            "softwareVersion": SOFTWARE_VERSION,
            "gameversion": gameversion,
            "gamebuild": gamebuild,
        },
        "message": msg,
    }


async def send_to_eddn(
    client: httpx.AsyncClient,
    event_data: dict,
    game_state: Optional[dict] = None,
    timeout: float = EDDN_TIMEOUT_SEC,
) -> bool:
    """Send event to EDDN using the shared httpx.AsyncClient. Pass game_state=CURRENT_SESSION."""
    payload = build_eddn_payload(event_data, game_state)

    if payload is None:
        return False  # Пакет не прошел валидацию (нет координат)

    logging.info(f"🚀 EDDN: Sending {event_data.get('event')}...")

    #print("\n--- [DEBUG] OUTGOING EDDN PAYLOAD START ---")
    #print(json.dumps(payload, indent=2, ensure_ascii=False))
    #print("--- [DEBUG] OUTGOING EDDN PAYLOAD END ---\n")

    try:
        response = await client.post(
            EDDN_UPLOAD_URL,
            json=payload,
            timeout=timeout,
        )
        if response.status_code == 200:
            logging.info("✅ EDDN: Upload Success")
            return True

        logging.warning(f"❌ EDDN: HTTP {response.status_code} - {response.text}")
        return False
    except httpx.HTTPError as e:
        logging.warning("⚠️ EDDN: Error %s", e)
        return False
    except Exception as e:
        logging.exception("Unexpected error in EDDN send")
        return False
