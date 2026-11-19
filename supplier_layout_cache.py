"""Supplier → dedicated parser cache.

Learn which deterministic layout parser worked for a supplier (EORI / VAT /
email domain), then skip AI on future invoices from the same fingerprint.

Gemini is only used as a *layout classifier* for unknown invoices — it returns
a parser_id hint, never line items. Manual export edits bump a correction
counter; after CORRECTION_FLAG_THRESHOLD edits the cached rule is flagged to
re-learn on the next successful dedicated parse.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path(__file__).resolve().parent / "supplier_layout_cache.db"

# Dedicated parsers owned by line_item_parser (not LLM / generic regex).
DEDICATED_PARSERS = frozenset({
    "rs_sitpro",
    "solarlux",
    "marlow",
    "vertical_table_ikf",
    "arrow_export",
    "xosil",
    "sugatsune",
    "tintometer",
    "yacht_package",
    "noc_scientific",
    "bas_commercial",
    "ati",
})

PARSER_HINTS = {
    "rs_sitpro": "RS Components / SITPRO — 'HS export code' on its own line",
    "solarlux": "Solarlux — Article no. + Comm. code columns",
    "marlow": "Marlow Ropes — Item No. / HS Code / UoM table",
    "vertical_table_ikf": "IKF / RS vertical — HS Codes + CofO + Stock No.",
    "arrow_export": "Arrow Export — HS embedded as (cc:XXXXXXXXXX)",
    "xosil": "Ocean Scientific — 'HS 9015 8080 00' spaced codes",
    "sugatsune": "Sugatsune Kogyo hardware invoice",
    "tintometer": "Tintometer / Lovibond — dotted HS 3822.90.0000",
    "yacht_package": "Yacht / shipyard package invoice with Value: £ headers",
    "noc_scientific": "NOC scientific packing list",
    "bas_commercial": "BAS / goods-description commodity table",
    "ati": "ATI — COC / HS Code / Unit of Measure / Line Weight",
}

CORRECTION_FLAG_THRESHOLD = 2


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS supplier_layouts (
                fingerprint TEXT PRIMARY KEY,
                fingerprint_kind TEXT,
                parser_id TEXT NOT NULL,
                supplier_label TEXT,
                success_count INTEGER NOT NULL DEFAULT 0,
                correction_count INTEGER NOT NULL DEFAULT 0,
                needs_regen INTEGER NOT NULL DEFAULT 0,
                last_parser_id TEXT,
                meta_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS supplier_corrections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL,
                parser_id TEXT,
                fields_changed INTEGER NOT NULL,
                sample_json TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_vat(raw: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (raw or "").upper())


def _norm_eori(raw: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (raw or "").upper())


def extract_supplier_fingerprint(text: str) -> Dict[str, Any]:
    """Best-effort supplier identity from invoice OCR/text."""
    blob = text or ""
    out: Dict[str, Any] = {
        "eori": None,
        "vat": None,
        "domain": None,
        "label": None,
        "fingerprint": None,
        "fingerprint_kind": None,
    }

    eori_m = re.search(
        r"\bEORI\s*(?:No\.?|Number|#)?\s*:?\s*(GB[A-Z0-9]{10,15})\b",
        blob,
        re.IGNORECASE,
    )
    if eori_m:
        out["eori"] = _norm_eori(eori_m.group(1))

    vat_m = re.search(
        r"\bVAT\s*(?:Registration\s*)?(?:No\.?|Number|#)?\s*:?\s*"
        r"((?:GB\s*)?\d{2,3}(?:\s*\d{4}){1,2}(?:\s*\d{2})?)",
        blob,
        re.IGNORECASE,
    )
    if vat_m:
        out["vat"] = _norm_vat(vat_m.group(1))

    dom_m = re.search(
        r"(?:www\.|https?://|@)([a-z0-9][a-z0-9.-]+\.[a-z]{2,})",
        blob,
        re.IGNORECASE,
    )
    if dom_m:
        host = dom_m.group(1).lower().removeprefix("www.")
        # Prefer seller-ish domains over generic mail hosts
        if host not in {"gmail.com", "outlook.com", "hotmail.com", "yahoo.com"}:
            out["domain"] = host

    # Light label for UI (Arrow Export, ATI, …)
    for label in (
        "Arrow Export",
        "Applied Technologies",
        "RS Components",
        "Marlow Ropes",
        "Solarlux",
        "Sugatsune",
        "Tintometer",
        "Lovibond",
    ):
        if re.search(re.escape(label), blob, re.IGNORECASE):
            out["label"] = label
            break

    if out["eori"]:
        out["fingerprint"] = f"eori:{out['eori']}"
        out["fingerprint_kind"] = "eori"
    elif out["vat"]:
        out["fingerprint"] = f"vat:{out['vat']}"
        out["fingerprint_kind"] = "vat"
    elif out["domain"]:
        out["fingerprint"] = f"domain:{out['domain']}"
        out["fingerprint_kind"] = "domain"

    return out


def get_cached_parser(fingerprint: str) -> Optional[Dict[str, Any]]:
    if not fingerprint:
        return None
    init_db()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM supplier_layouts WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchone()
        if not row:
            return None
        return dict(row)
    finally:
        conn.close()


def record_parse_success(
    fingerprint_info: Dict[str, Any],
    parser_id: str,
    *,
    source: str = "detect",
) -> None:
    """Remember that this supplier's invoice was parsed by parser_id."""
    fp = (fingerprint_info or {}).get("fingerprint")
    if not fp or parser_id not in DEDICATED_PARSERS:
        return
    init_db()
    now = _now()
    kind = fingerprint_info.get("fingerprint_kind") or ""
    label = fingerprint_info.get("label") or ""
    meta = json.dumps({"source": source}, separators=(",", ":"))
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT success_count, needs_regen FROM supplier_layouts WHERE fingerprint = ?",
            (fp,),
        ).fetchone()
        if row:
            # Successful dedicated parse clears the regen flag
            conn.execute(
                """
                UPDATE supplier_layouts
                SET parser_id = ?, last_parser_id = ?, success_count = success_count + 1,
                    needs_regen = 0, supplier_label = COALESCE(NULLIF(?, ''), supplier_label),
                    meta_json = ?, updated_at = ?
                WHERE fingerprint = ?
                """,
                (parser_id, parser_id, label, meta, now, fp),
            )
        else:
            conn.execute(
                """
                INSERT INTO supplier_layouts (
                    fingerprint, fingerprint_kind, parser_id, supplier_label,
                    success_count, correction_count, needs_regen, last_parser_id,
                    meta_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, 0, 0, ?, ?, ?, ?)
                """,
                (fp, kind, parser_id, label, parser_id, meta, now, now),
            )
        conn.commit()
    finally:
        conn.close()


def record_export_corrections(
    fingerprint: str,
    *,
    parser_id: str = "",
    fields_changed: int = 0,
    sample: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Log manual edits from the Step-2 editor; flag rule if edits pile up."""
    if not fingerprint or fields_changed <= 0:
        return {"flagged": False, "correction_count": 0}
    init_db()
    now = _now()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO supplier_corrections
                (fingerprint, parser_id, fields_changed, sample_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                fingerprint,
                parser_id or None,
                fields_changed,
                json.dumps(sample or [], separators=(",", ":"))[:4000],
                now,
            ),
        )
        row = conn.execute(
            "SELECT correction_count FROM supplier_layouts WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchone()
        if row:
            new_count = int(row["correction_count"]) + 1
            flagged = 1 if new_count >= CORRECTION_FLAG_THRESHOLD else 0
            conn.execute(
                """
                UPDATE supplier_layouts
                SET correction_count = ?, needs_regen = CASE
                    WHEN ? = 1 THEN 1 ELSE needs_regen END,
                    updated_at = ?
                WHERE fingerprint = ?
                """,
                (new_count, flagged, now, fingerprint),
            )
            conn.commit()
            return {"flagged": bool(flagged), "correction_count": new_count}
        conn.commit()
        return {"flagged": False, "correction_count": 0}
    finally:
        conn.close()


def count_item_field_edits(
    original: List[Dict[str, Any]],
    edited: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Count rows where key customs fields changed between parse and export."""
    watch = (
        "commodity_code",
        "description",
        "quantity",
        "total_value",
        "country_of_origin",
        "net_weight",
    )
    changed_rows = 0
    samples: List[Dict[str, Any]] = []
    n = min(len(original or []), len(edited or []))
    for i in range(n):
        o, e = original[i], edited[i]
        diffs = {}
        for k in watch:
            ov = str(o.get(k) or "").strip()
            ev = str(e.get(k) or "").strip()
            if ov != ev:
                diffs[k] = {"from": ov[:80], "to": ev[:80]}
        if diffs:
            changed_rows += 1
            if len(samples) < 5:
                samples.append({
                    "line": e.get("line_number") or o.get("line_number") or i + 1,
                    "diffs": diffs,
                })
    # Rows added/removed in the editor
    changed_rows += abs(len(edited or []) - len(original or []))
    return {"fields_changed": changed_rows, "sample": samples}


def classify_layout_hint(
    first_page_text: str,
    api_key: str,
    *,
    model: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Ask Gemini which dedicated parser_id fits — never extract line items."""
    if not api_key or not (first_page_text or "").strip():
        return None
    try:
        import google.generativeai as genai
    except ImportError:
        return None

    catalog = "\n".join(f"- {pid}: {desc}" for pid, desc in PARSER_HINTS.items())
    prompt = (
        "You classify UK commercial-invoice layouts for a customs tool.\n"
        "Choose the single best parser_id from this catalog, or null if none fit.\n"
        "Return ONLY JSON: "
        '{"parser_id": "arrow_export"|null, "confidence": 0.0-1.0, "reason": "short"}\n'
        "Do NOT extract line items or HS codes.\n\n"
        f"Catalog:\n{catalog}\n\n"
        f"Invoice first-page text (truncated):\n{(first_page_text or '')[:6000]}"
    )
    try:
        genai.configure(api_key=api_key)
        mdl = genai.GenerativeModel(model or "gemini-2.5-flash")
        resp = mdl.generate_content(
            prompt,
            generation_config={"temperature": 0.1, "response_mime_type": "application/json"},
        )
        raw = (getattr(resp, "text", None) or "").strip()
        if not raw:
            return None
        data = json.loads(raw)
        pid = data.get("parser_id")
        if pid and pid not in DEDICATED_PARSERS:
            data["parser_id"] = None
        conf = float(data.get("confidence") or 0)
        data["confidence"] = conf
        return data
    except Exception as exc:
        print(f"[LayoutCache] Gemini classify failed: {exc}")
        return None
