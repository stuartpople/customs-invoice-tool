"""
Streamlit UI for Job-Based PDF Processing
Stable, resumable, with real-time progress tracking
"""
import streamlit as st # type: ignore
import threading
import time
import json
from pathlib import Path
from datetime import datetime
import pandas as pd # type: ignore
from job_processor import JobProcessor
from line_item_parser import LineItemParser
from consolidation import group_by_commodity_code, create_consolidated_dataframe, export_to_excel
from excel_export import create_comprehensive_export
from cds_csv_export import create_cds_excel
from hmrc_api import HMRCTariffAPI
from countries import COUNTRIES, COMMON_COUNTRIES, COUNTRY_TO_ISO
from file_extractor import extract_from_file
import shutil
from streamlit_autorefresh import st_autorefresh # type: ignore

# Version tracking for cache busting
APP_VERSION = "v3.8-editable"

st.set_page_config(
    page_title="LogistiCore | CDS Customs Invoice Tool",
    layout="wide",
    page_icon="🚢"
)

# LogistiCore branding CSS
st.markdown("""
<style>
    /* ── LogistiCore brand colours ── */
    :root {
        --lc-navy:   #0a1628;
        --lc-teal:   #00b4d8;
        --lc-teal2:  #0077b6;
        --lc-light:  #e8f4fb;
        --lc-white:  #ffffff;
    }

    /* Branded header banner */
    .lc-header {
        background: linear-gradient(135deg, #0a1628 0%, #0d2040 60%, #0077b6 100%);
        color: #ffffff;
        padding: 1.1rem 2rem;
        border-radius: 10px;
        margin-bottom: 1.2rem;
        display: flex;
        align-items: center;
        gap: 1rem;
        box-shadow: 0 4px 16px rgba(0,0,0,0.18);
    }
    .lc-header h1 {
        margin: 0;
        font-size: 1.6rem;
        font-weight: 800;
        letter-spacing: -0.5px;
        color: #ffffff;
    }
    .lc-header h1 span {
        color: #00b4d8;
    }
    .lc-header p {
        margin: 0.15rem 0 0 0;
        font-size: 0.85rem;
        color: #a8c8d8;
    }
    .lc-badge {
        background: #00b4d8;
        color: #0a1628;
        font-size: 0.7rem;
        font-weight: 700;
        padding: 0.2rem 0.55rem;
        border-radius: 20px;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        white-space: nowrap;
        align-self: flex-start;
        margin-top: 0.3rem;
    }

    /* Cards */
    .metric-card {
        background: var(--lc-white);
        padding: 1.5rem;
        border-radius: 10px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.08);
        border-left: 4px solid var(--lc-teal);
        margin: 1rem 0;
    }

    /* Progress bar */
    .stProgress > div > div {
        background: linear-gradient(90deg, #0077b6 0%, #00b4d8 100%);
    }

    /* Section headers */
    h2, h3 { color: #0a1628; }

    /* Primary buttons */
    .stButton>button[kind="primary"] {
        background: linear-gradient(135deg, #0077b6, #00b4d8);
        border: none;
        color: #ffffff;
        border-radius: 8px;
        font-weight: 600;
    }
    .stButton>button {
        border-radius: 8px;
        font-weight: 600;
        transition: all 0.2s;
    }
    .stButton>button:hover {
        transform: translateY(-1px);
        box-shadow: 0 4px 12px rgba(0,180,216,0.3);
    }

    /* Sidebar */
    [data-testid="stSidebar"] { background: #0a1628; }
    [data-testid="stSidebar"] * { color: #e0eef5 !important; }
    [data-testid="stSidebar"] .stSelectbox label { color: #a8c8d8 !important; }
</style>
""", unsafe_allow_html=True)

# Initialize job processor, parser, and HMRC API
processor = JobProcessor()
parser = LineItemParser()
hmrc_api = HMRCTariffAPI()

# Initialize session state
if 'current_job_id' not in st.session_state:
    st.session_state.current_job_id = None
if 'processing_started' not in st.session_state:
    st.session_state.processing_started = False
if 'username' not in st.session_state:
    import getpass
    import os
    try:
        st.session_state.username = getpass.getuser()
    except:
        st.session_state.username = os.getenv('USERNAME', os.getenv('USER', 'System User'))
if 'non_pdf_processed' not in st.session_state:
    st.session_state.non_pdf_processed = False
if 'line_items' not in st.session_state:
    st.session_state.line_items = []
if 'invoice_metadata' not in st.session_state:
    st.session_state.invoice_metadata = {}
if 'uploader_key' not in st.session_state:
    st.session_state.uploader_key = 0
if 'last_uploaded_names' not in st.session_state:
    st.session_state.last_uploaded_names = set()

st.markdown("""
<div class="lc-header">
  <div style="font-size:2.2rem;">🚢</div>
  <div style="flex:1">
    <h1>Logisti<span>Core</span> &nbsp;<small style="font-size:0.85rem;font-weight:400;color:#a8c8d8;">Technologies</small></h1>
    <p>CDS Customs Invoice Tool &mdash; HMRC-connected &middot; Template-based worksheet export</p>
  </div>
  <div class="lc-badge">UK CDS</div>
</div>
""", unsafe_allow_html=True)

# Version check and cache clear
if 'app_version' not in st.session_state or st.session_state.app_version != APP_VERSION:
    # Clear HMRC cache on version change
    keys_to_clear = ['hmrc_results', 'hmrc_consolidate_state', 'hmrc_dest_country', 'parsed_items', 'parsed_job_ids', 'hs_validation_results']
    for key in keys_to_clear:
        if key in st.session_state:
            del st.session_state[key]
    st.session_state.app_version = APP_VERSION
    st.success(f"🔄 App updated to {APP_VERSION} - HMRC cache cleared!")

# Show version in sidebar
with st.sidebar:
    st.caption(f"Version: {APP_VERSION}")

# Auto-refresh every 2 seconds if processing
if st.session_state.processing_started and st.session_state.current_job_id:
    try:
        progress_data = processor.get_job_progress(st.session_state.current_job_id)
        status = progress_data.get('status', '')
        if status in ["processing", "pending", "created"]:
            # Auto-refresh every 2 seconds while processing
            st_autorefresh(interval=2000, limit=1000, key="auto_refresh")
    except Exception as e:
        # Continue refreshing even if there's an error checking status
        if st.session_state.processing_started:
            st_autorefresh(interval=2000, limit=1000, key="auto_refresh")

st.divider()

# Step 1: Setup
st.header("📋 Step 1: Job Setup")

col1, col2 = st.columns(2)

with col1:
    # Username captured automatically from system
    username = st.session_state.username
    st.info(f"👤 Logged in as: **{username}**")
    
    direction = st.selectbox(
        "Trade Direction",
        options=["Export", "Import"],
        help="Export = 8 digits, Import = 10 digits"
    )

with col2:
    # Country dropdown with common countries at top
    country_options = [""] + COMMON_COUNTRIES + ["---"] + COUNTRIES
    # Label changes based on direction
    country_label = "Origin Country" if direction == "Import" else "Destination Country"
    country_help = "Country of origin for goods" if direction == "Import" else "Country of destination for goods"
    country = st.selectbox(
        country_label,
        options=country_options,
        help=country_help
    )
    
    uploaded_files = st.file_uploader(
        "Upload Invoice Files",
        type=['pdf', 'xlsx', 'xls', 'docx', 'doc'],
        accept_multiple_files=True,
        help="Upload multiple PDFs, Excel, or Word files - all will be processed together",
        key=f"file_uploader_{st.session_state.uploader_key}"
    )
    
    # Detect when user uploads new / different files and reset processing state
    current_names = {f.name for f in uploaded_files} if uploaded_files else set()
    if current_names and current_names != st.session_state.last_uploaded_names:
        st.session_state.last_uploaded_names = current_names
        if st.session_state.processing_started and not st.session_state.get('_processing_now'):
            st.session_state.processing_started = False
            st.session_state.non_pdf_processed = False
            st.session_state.line_items = []
            st.session_state.invoice_metadata = {}
            st.session_state.current_job_id = None
            st.session_state.pdf_job_ids = []
            st.rerun()
    
    # Add button to clear uploaded files
    if uploaded_files:
        col_clear1, col_clear2 = st.columns([1, 3])
        with col_clear1:
            if st.button("🗑️ Clear Files", help="Clear uploaded files to start fresh"):
                st.session_state.uploader_key += 1
                st.session_state.current_job_id = None
                st.session_state.processing_started = False
                st.session_state.line_items = []
                st.session_state.invoice_metadata = {}
                st.rerun()

st.divider()

# Step 2: Start Processing
if uploaded_files and username:
    # Show summary of uploaded files
    total_size = sum(f.size for f in uploaded_files) / 1024 / 1024
    pdf_files = [f for f in uploaded_files if f.name.lower().endswith('.pdf')]
    non_pdf_files = [f for f in uploaded_files if not f.name.lower().endswith('.pdf')]
    
    st.success(f"✅ Ready to process **{len(uploaded_files)} file(s)** ({total_size:.1f} MB total)")
    
    if len(uploaded_files) > 1:
        st.info(f"📄 {len(pdf_files)} PDF(s) | 📊 {len(non_pdf_files)} Excel/Word file(s)")
        with st.expander("📋 View uploaded files"):
            for f in uploaded_files:
                st.text(f"• {f.name} ({f.size/1024:.1f} KB)")
    
    # Show info for uploaded Excel files (if any)
    excel_files = [f for f in uploaded_files if f.name.lower().endswith(('.xlsx', '.xls'))]
    if excel_files:
        st.subheader("📊 CDS Excel Files Validation")
        for excel_file in excel_files:
            with st.container(border=True):
                col1, col2 = st.columns([2, 1])
                
                with col1:
                    st.success(f"✅ {excel_file.name}")
                
                # Try to extract entry type and type info from Excel
                try:
                    df = pd.read_excel(excel_file, sheet_name=0, nrows=10)
                    
                    # Look for common header patterns (adjust if needed)
                    entry_type = None
                    trade_type = None
                    
                    # Search for entry type in first few rows/columns
                    for col_idx, col in enumerate(df.columns):
                        for row_idx in range(min(5, len(df))):
                            cell_val = str(df.iloc[row_idx, col_idx]).strip()
                            if cell_val in ('B1', 'H1', 'B1 ', 'H1 '):
                                entry_type = cell_val.strip()
                            if cell_val in ('E', 'I', 'E ', 'I '):
                                trade_type = cell_val.strip()
                    
                    # Display extracted info
                    if col2:
                        with col2:
                            if entry_type or trade_type:
                                st.info(f"📋 Entry Type: {entry_type or '?'}\n📋 Type: {trade_type or '?'}")
                            
                                # Show what it means
                                if entry_type == 'B1':
                                    st.caption("🚚 Export (B1)")
                                elif entry_type == 'H1':
                                    st.caption("📦 Import (H1)")
                                
                                if trade_type == 'E':
                                    st.caption("📤 Export")
                                elif trade_type == 'I':
                                    st.caption("📥 Import")
                    
                except Exception as e:
                    st.warning(f"⚠️ Could not extract header info from {excel_file.name}: {str(e)[:100]}")
    
    # Only disable button while a PDF background job is still running
    _has_active_pdf_job = False
    if st.session_state.processing_started and st.session_state.get('current_job_id'):
        try:
            _prog = processor.get_job_progress(st.session_state.current_job_id)
            _has_active_pdf_job = _prog.get('status', '') in ('processing', 'pending', 'created')
        except Exception:
            pass
    
    if st.button("🚀 Start Processing", type="primary", disabled=_has_active_pdf_job):
        # Guard against rerun interference
        st.session_state._processing_now = True
        
        # Reset state for fresh processing
        st.session_state.processing_started = False
        st.session_state.non_pdf_processed = False
        st.session_state.line_items = []
        st.session_state.invoice_metadata = {}
        
        # Process all files
        all_items = []
        pdf_job_ids = []
        
        # Validate Excel files first
        excel_files = [f for f in non_pdf_files if f.name.lower().endswith(('.xlsx', '.xls'))]
        
        # First, process non-PDF files (instant)
        if non_pdf_files:
            progress_bar = st.progress(0, text="🔍 Extracting data from Excel/Word file(s)...")
            total_files = len(non_pdf_files)
            for i, uploaded_file in enumerate(non_pdf_files):
                progress_bar.progress(
                    int((i / total_files) * 80),
                    text=f"🔍 Processing {uploaded_file.name} ({i+1}/{total_files})..."
                )
                try:
                    text, items, metadata = extract_from_file(uploaded_file, uploaded_file.name, trade_direction=direction)
                    
                    # Check for Excel metadata (entry type, type codes)
                    excel_metadata = None
                    actual_items = []
                    
                    if items:
                        # Filter out metadata item if present
                        for item in items:
                            if isinstance(item, dict) and item.get('_excel_metadata'):
                                excel_metadata = {k: v for k, v in item.items() if not k.startswith('_')}
                            else:
                                actual_items.append(item)
                        
                        # Validate Excel metadata against expected direction
                        if excel_metadata:
                            entry_type = excel_metadata.get('entry_type', '')
                            detected_dir = excel_metadata.get('direction_detected', '')
                            type_code = excel_metadata.get('type', '')
                            
                            # Check consistency
                            is_export = direction.lower() == 'export'
                            expected_entry = 'B1' if is_export else 'H1'
                            expected_type = 'E' if is_export else 'I'
                            
                            validation_issues = []
                            if entry_type and entry_type != expected_entry:
                                validation_issues.append(f"Entry Type is '{entry_type}' but selected direction is '{direction}'")
                            if type_code and type_code != expected_type:
                                validation_issues.append(f"Type is '{type_code}' but expected '{expected_type}' for {direction}")
                            
                            if validation_issues:
                                st.warning(f"⚠️ **Potential mismatch in {uploaded_file.name}:**")
                                for issue in validation_issues:
                                    st.caption(f"  • {issue}")
                            elif entry_type or type_code:
                                st.success(f"✅ {uploaded_file.name}: Entry Type={entry_type}, Type={type_code}")
                        
                        if actual_items:
                            all_items.extend(actual_items)
                            # Store metadata from first file
                            if not st.session_state.invoice_metadata:
                                st.session_state.invoice_metadata = metadata
                            st.success(f"✅ Extracted {len(actual_items)} items from {uploaded_file.name}")
                        else:
                            st.warning(f"⚠️ No data rows extracted from {uploaded_file.name}. Only metadata found.")
                    else:
                        st.warning(f"⚠️ No items extracted from {uploaded_file.name}. The file layout may not be recognised. Debug: {text[:200] if text else 'No text'}")
                except Exception as e:
                    import traceback
                    st.error(f"❌ Error processing {uploaded_file.name}: {e}")
                    st.code(traceback.format_exc(), language="text")
            progress_bar.progress(100, text="✅ Excel/Word extraction complete!")
            import time; time.sleep(1)  # Brief pause so user sees completion
        
        # Then, process PDFs (background)
        if pdf_files:
            for uploaded_file in pdf_files:
                # Create job for each PDF
                job_id = processor.create_job(
                    pdf_path="temp.pdf",
                    username=username,
                    direction=direction.lower(),
                    country=country
                )
                
                # Save uploaded PDF
                job_dir = processor.get_job_dir(job_id)
                pdf_save_path = job_dir / "original" / uploaded_file.name
                with open(pdf_save_path, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                
                # Update metadata with actual path
                processor.update_job_metadata(job_id, {"pdf_path": str(pdf_save_path)})
                
                pdf_job_ids.append(job_id)
                
                # Start background processing thread
                def process_in_background(jid, path):
                    try:
                        processor.process_job(jid, str(path))
                    except Exception as e:
                        processor.update_job_metadata(jid, {
                            "status": "error",
                            "error": str(e)
                        })
                
                thread = threading.Thread(target=process_in_background, args=(job_id, pdf_save_path), daemon=True)
                thread.start()
            
            # Store PDF job IDs for tracking
            st.session_state.pdf_job_ids = pdf_job_ids
            st.session_state.current_job_id = pdf_job_ids[0] if pdf_job_ids else None
        
        # Store non-PDF items if any
        if all_items:
            st.session_state.line_items = all_items
            st.session_state.non_pdf_processed = True
            st.session_state.process_message = f"✅ Extracted {len(all_items)} items"
        elif non_pdf_files:
            # Non-PDF files were uploaded but yielded zero items
            st.session_state.non_pdf_processed = True  # Still show results section
            st.session_state.process_message = "⚠️ No items could be extracted. Check the file format."
        
        st.session_state.processing_started = True
        st.session_state._processing_now = False
        st.rerun()

elif not username:
    st.warning("⚠️ Please enter your username")
elif not uploaded_files and not st.session_state.processing_started:
    st.info("📤 Please upload a file to begin (PDF, Excel, or Word)")

st.divider()

# Display results for non-PDF files (Excel/Word)
if st.session_state.get('non_pdf_processed', False):
    st.header("📊 Extracted Line Items")
    
    # Show any stored processing messages
    if st.session_state.get('process_message'):
        msg = st.session_state.process_message
        if msg.startswith("✅"):
            st.success(msg)
        elif msg.startswith("⚠️"):
            st.warning(msg)
        else:
            st.info(msg)
    
    items = st.session_state.get('line_items', [])
    
    if items:
        st.success(f"✅ **Extracted {len(items)} raw items from Excel**")
        
        # Convert to DataFrame and display raw items
        df_items = pd.DataFrame(items)
        
        # Reorder columns for raw display
        cols_order = ['description', 'commodity_code', 'quantity', 'uom', 
                    'total_value', 'country_of_origin', 'net_weight']
        display_cols = [c for c in cols_order if c in df_items.columns]
        df_display = df_items[display_cols].copy()
        
        with st.expander("📋 View Raw Items (before consolidation)", expanded=False):
            st.dataframe(df_display, use_container_width=True, height=300)

        # ── Auto-validate HS codes against HMRC Trade Tariff ──────────────────
        hs_validation_key = 'hs_validation_results'
        if hs_validation_key not in st.session_state:
            unique_codes = list(set(
                it.get('commodity_code', '') for it in items
                if it.get('commodity_code')
            ))
            if unique_codes:
                with st.spinner(f"Validating {len(unique_codes)} HS codes against HMRC tariff..."):
                    try:
                        validation = hmrc_api.validate_commodity_codes(
                            unique_codes, direction=direction.lower()
                        )
                        st.session_state[hs_validation_key] = validation
                    except Exception:
                        st.session_state[hs_validation_key] = {}

        hs_validation = st.session_state.get(hs_validation_key, {})

        def _desc_match_score_xl(item_desc: str, tariff_desc: str) -> float:
            import re as _re
            stop = {'for', 'of', 'or', 'and', 'the', 'a', 'an', 'in',
                    'to', 'with', 'not', 'more', 'than', 'other', 'use',
                    'but', 'on', 'by', 'at', 'from', 'no', 'its', 'is'}
            def _tokens(s):
                s = _re.sub(r'<[^>]+>', ' ', s)
                return {w for w in _re.findall(r'[a-z]{2,}', s.lower()) if w not in stop}
            a = _tokens(item_desc)
            b = _tokens(tariff_desc)
            if not a or not b:
                return 0.0
            return len(a & b) / min(len(a), len(b))

        invalid_codes = {c: v for c, v in hs_validation.items()
                         if not v.get('valid', True)}
        auto_fixed: dict = {}
        auto_fixed_desc: dict = {}
        still_invalid: dict = {}
        is_export_xl = direction.lower() == 'export'

        for code, result in invalid_codes.items():
            resolved = result.get('resolved_code')
            if resolved:
                auto_fixed[code] = resolved[:8] if is_export_xl else resolved
                auto_fixed_desc[code] = result.get('description', '')
            else:
                still_invalid[code] = result

        # Description-based matching for multi-candidate codes
        desc_resolved_info: dict = {}
        for code, result in list(still_invalid.items()):
            candidates = result.get('candidates', [])
            if not candidates:
                continue
            item_descs = [it.get('description', '') for it in items
                          if it.get('commodity_code') == code]
            if not item_descs:
                continue
            all_matched = True
            matches = []
            for idesc in item_descs:
                scores = [(_desc_match_score_xl(idesc, c['description']), c) for c in candidates]
                scores.sort(key=lambda x: x[0], reverse=True)
                best_score, best_cand = scores[0]
                if best_score >= 0.25:
                    new_code = best_cand['code'][:8] if is_export_xl else best_cand['code']
                    matches.append((idesc, new_code, best_cand['description'], best_score))
                else:
                    other_cands = [c for c in candidates
                                   if c['description'].strip().lower() == 'other']
                    if other_cands:
                        oc = other_cands[-1]
                        new_code = oc['code'][:8] if is_export_xl else oc['code']
                        matches.append((idesc, new_code, 'Other (fallback)', 0.0))
                    else:
                        all_matched = False
                        matches.append((idesc, None, None, 0.0))
            if all_matched and matches:
                desc_resolved_info[code] = matches
                del still_invalid[code]

        if desc_resolved_info:
            for code, match_list in desc_resolved_info.items():
                idx = 0
                for item in items:
                    if item.get('commodity_code') != code:
                        continue
                    if idx < len(match_list):
                        _, new_code, tdesc, score = match_list[idx]
                        idx += 1
                        if new_code:
                            old = item['commodity_code']
                            item['commodity_code'] = new_code
                            note = f"HS auto-classified: {old} → {new_code} ({tdesc[:50]}, score={score:.2f})"
                            existing = item.get('review_notes', '')
                            if note not in (existing or ''):
                                item['review_notes'] = f"{existing}; {note}" if existing else note
                            auto_fixed[old] = new_code
                            auto_fixed_desc[old] = tdesc or ''

        if auto_fixed:
            for item in items:
                cc = item.get('commodity_code', '')
                if cc in auto_fixed:
                    old = cc
                    item['commodity_code'] = auto_fixed[cc]
                    note = f"HS code auto-corrected: {old} → {auto_fixed[cc]}"
                    existing = item.get('review_notes', '')
                    if note not in (existing or ''):
                        item['review_notes'] = f"{existing}; {note}" if existing else note
            st.warning(f"🔄 **{len(auto_fixed)} HS code(s) auto-corrected:**")
            with st.expander("Auto-correction Details", expanded=True):
                for old_c, new_c in sorted(auto_fixed.items()):
                    desc = auto_fixed_desc.get(old_c, '')
                    desc_short = f" — {desc[:60]}" if desc else ""
                    st.markdown(f"- `{old_c}` → `{new_c}`{desc_short}")

        if still_invalid:
            for item in items:
                cc = item.get('commodity_code', '')
                if cc in still_invalid:
                    item['needs_review'] = True
                    existing = item.get('review_notes', '')
                    msg = still_invalid[cc].get('error', 'Invalid HS code')
                    if msg not in (existing or ''):
                        item['review_notes'] = f"{existing}; {msg}" if existing else msg
            st.error(
                f"❌ **{len(still_invalid)} HS code(s) could not be resolved — manual review needed:** "
                + ", ".join(f"`{c}`" for c in sorted(still_invalid))
            )
            with st.expander("Unresolved HS Code Details"):
                for c, v in sorted(still_invalid.items()):
                    st.markdown(f"- **{c}**: {v.get('error', 'Invalid')}")

        if not invalid_codes and hs_validation:
            st.success(f"✅ All {len(hs_validation)} HS codes validated against HMRC tariff")

        needs_review = [item for item in items if item.get('needs_review')]
        if needs_review:
            st.warning(f"⚠️ **{len(needs_review)} items need manual review**")
            with st.expander("📋 Items Needing Review", expanded=False):
                for item in needs_review:
                    st.markdown(
                        f"**{item.get('description', 'N/A')[:60]}** — "
                        f"HS `{item.get('commodity_code', 'N/A')}` — "
                        f"⚠️ *{item.get('review_notes', 'Low confidence')}*"
                    )
                    st.divider()

        # Consolidation
        st.divider()
        st.subheader("📦 Consolidation")
        
        consolidation_mode = st.radio(
            "Consolidation mode",
            options=["HS Code + Country of Origin", "HS Code only", "No consolidation"],
            index=0,
            horizontal=True,
            help="Choose how to group items into declaration lines"
        )
        
        if consolidation_mode != "No consolidation":
            split_by_country = (consolidation_mode == "HS Code + Country of Origin")
            grouped = group_by_commodity_code(items, group_by_origin=split_by_country)
            consolidated_items = []
            for code_key, group_items in grouped.items():
                from consolidation import consolidate_items
                consolidated = consolidate_items(group_items)
                # Handle composite key "HS_CODE|COUNTRY" from group_by_origin
                if code_key.startswith('__BLANK_'):
                    display_code = ''
                elif '|' in code_key:
                    display_code = code_key.split('|')[0]
                else:
                    display_code = code_key
                consolidated_items.append({
                    'commodity_code': display_code,
                    'description': f"{consolidated['description']} ({consolidated['item_count']} items)" if consolidated['item_count'] > 1 else consolidated['description'],
                    'quantity': consolidated['total_quantity'],
                    'uom': group_items[0].get('uom', 'PCS'),
                    'total_value': consolidated['total_value'],
                    'net_weight': consolidated['total_net_weight'],
                    'country_of_origin': ', '.join(consolidated['countries_of_origin']),
                    'currency': group_items[0].get('currency', 'GBP'),
                })
            
            display_items = consolidated_items
            label = "HS code + origin" if split_by_country else "HS code"
            st.info(f"📦 **Consolidated {len(items)} items into {len(consolidated_items)} declaration lines** (grouped by {label})")
        else:
            display_items = items
        
        # Show consolidated/final items table
        df_final = pd.DataFrame(display_items)
        final_cols = ['commodity_code', 'description', 'quantity', 'uom', 'total_value', 'country_of_origin', 'net_weight']
        df_final_display = df_final[[c for c in final_cols if c in df_final.columns]]
        st.dataframe(df_final_display, use_container_width=True, height=400)
        
        # --- HMRC Enrichment & Export (same as PDF path) ---
        st.divider()
        st.subheader("🔍 HMRC Enrichment & Export")
        
        # Use direction from selectbox (local variable set in Step 1)
        current_direction = direction.lower()
        
        if current_direction == 'export':
            export_only_measures = st.checkbox("📤 Export measures only", value=True, 
                help="Filter to show only export-related measures (Export control, Export authorization)")
        else:
            st.info("💡 Import mode: Will show duties, VAT, suspensions, and restrictions")
            export_only_measures = False
        
        col_hmrc1, col_hmrc2 = st.columns(2)
        
        with col_hmrc1:
            if st.button("🔍 Lookup HMRC Data", type="primary", use_container_width=True,
                         key="excel_hmrc_lookup"):
                # Get unique commodity codes from the final items
                codes = list(set(it.get('commodity_code', '') for it in display_items if it.get('commodity_code')))
                
                # Map country name to ISO code via HMRC-sourced lookup
                dest_country_code = COUNTRY_TO_ISO.get(country, country[:2].upper() if country else None)
                
                with st.spinner(f"Looking up {len(codes)} commodity codes in HMRC API..."):
                    hmrc_results = {}
                    progress_bar = st.progress(0)
                    for idx, code in enumerate(codes):
                        data = hmrc_api.get_commodity_details(
                            code, 
                            direction=current_direction,
                            destination_country=dest_country_code,
                            export_only=export_only_measures
                        )
                        hmrc_results[code] = data
                        progress_bar.progress((idx + 1) / len(codes))
                    
                    st.session_state.hmrc_results = hmrc_results
                    st.rerun()
        
        with col_hmrc2:
            hmrc_results = st.session_state.get('hmrc_results', None)
            
            if hmrc_results:
                if st.button("🔄 Clear & Re-lookup", use_container_width=True,
                             key="excel_hmrc_clear",
                             help="Clear cached results and lookup again"):
                    del st.session_state.hmrc_results
                    st.rerun()
            
            if st.button("📥 Export to Excel", use_container_width=True,
                         key="excel_export_btn"):
                # Create comprehensive Excel export with HMRC data
                _inv_meta = st.session_state.get('invoice_metadata', {})
                _inv_meta = {**_inv_meta, 'cpc_code': '1040' if current_direction == 'export' else '4000'}
                excel_bytes = create_comprehensive_export(
                    items=display_items,
                    hmrc_data=hmrc_results,
                    direction=current_direction,
                    country='',
                    consolidate=False,  # Already consolidated above
                    metadata=_inv_meta
                )
                _inv_ref = _inv_meta.get('invoice_number') or ''
                _excel_fname = f"CDS-Customs-Entry-Worksheet-{_inv_ref}.xlsx" if _inv_ref else "CDS-Customs-Entry-Worksheet.xlsx"
                st.download_button(
                    "📥 Download Excel File",
                    data=excel_bytes.getvalue(),
                    file_name=_excel_fname,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key="excel_download_btn"
                )

            if st.button("📋 FCL Excel Sheet", use_container_width=True,
                         key="excel_cds_btn",
                         help="Export in CDS Customs Entry Worksheet format using the same consolidation as above."):
                # FCL export uses the same consolidation as standard Excel
                _inv_meta = st.session_state.get('invoice_metadata', {})
                _inv_meta = {**_inv_meta, 'cpc_code': '1040' if current_direction == 'export' else '4000'}
                cds_bytes = create_cds_excel(
                    items=display_items,
                    direction=current_direction,
                    hmrc_data=hmrc_results,
                    consolidate=False,  # Items already consolidated by user's choice above
                    metadata=_inv_meta
                )
                _inv_ref = _inv_meta.get('invoice_number') or ''
                _fcl_fname = f"CDS-Customs-Entry-Worksheet-FCL-{_inv_ref}.xlsx" if _inv_ref else "CDS-Customs-Entry-Worksheet-FCL.xlsx"
                st.download_button(
                    "📋 Download FCL Excel Sheet",
                    data=cds_bytes.getvalue(),
                    file_name=_fcl_fname,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key="excel_cds_download_btn"
                )
        
        # Show HMRC results if available
        if hmrc_results:
            st.divider()
            st.subheader("📋 HMRC Tariff Information")
            st.info(f"💡 **{len(hmrc_results)} codes looked up.** Use 'Export to Excel' to download with document codes.")
            
            for code, data in hmrc_results.items():
                with st.expander(f"📦 {code} - {data.get('description', 'N/A')[:80]}"):
                    if 'error' in data:
                        st.error(f"❌ {data['error']}")
                    else:
                        col_a, col_b = st.columns(2)
                        with col_a:
                            st.markdown(f"**Description:** {data.get('description', 'N/A')}")
                            supp = data.get('supplementary_units', 'Not required')
                            st.markdown(f"**Supplementary Units:** {supp}")
                        with col_b:
                            # Show document code groups with selection
                            groups = data.get('document_code_groups', [])
                            selected = data.get('selected_document_codes', {})
                            doc_codes = data.get('document_codes', {})

                            if groups:
                                st.markdown("**Document Codes:**")
                                for gi, grp in enumerate(groups):
                                    codes_in_group = grp.get('codes', [])
                                    if len(codes_in_group) <= 1:
                                        # Single code — no choice needed
                                        c = codes_in_group[0]
                                        st.markdown(f"- ✅ `{c['code']}`: {c['requirement'][:80]}")
                                    else:
                                        # Multiple alternatives — let user pick
                                        options = [c['code'] for c in codes_in_group]
                                        labels = [f"{c['code']} — {c['requirement'][:60]}" for c in codes_in_group]
                                        # Default to the auto-selected one
                                        default_idx = 0
                                        for idx, c in enumerate(codes_in_group):
                                            if c['code'] in selected:
                                                default_idx = idx
                                                break
                                        chosen = st.selectbox(
                                            grp.get('measure', 'Document Code')[:50],
                                            labels,
                                            index=default_idx,
                                            key=f"dc_{code}_{gi}"
                                        )
                                        # Update selected in session state
                                        chosen_code = options[labels.index(chosen)]
                                        chosen_req = codes_in_group[labels.index(chosen)]['requirement']
                                        if 'hmrc_results' in st.session_state:
                                            st.session_state.hmrc_results[code]['selected_document_codes'][chosen_code] = chosen_req
                            elif doc_codes:
                                st.markdown("**Document Codes:**")
                                for dc, req in doc_codes.items():
                                    st.markdown(f"- `{dc}`: {req[:80]}")
                            else:
                                st.markdown("**Document Codes:** None required")
                            
                            if current_direction == 'import':
                                duty = data.get('third_country_duty', 'N/A')
                                vat = data.get('vat_rate', 'N/A')
                                st.markdown(f"**Third Country Duty:** {duty}")
                                st.markdown(f"**VAT Rate:** {vat}")
        
        # Reset button
        st.divider()
        if st.button("🔄 Process Another File", use_container_width=True, key="excel_reset"):
            st.session_state.non_pdf_processed = False
            st.session_state.line_items = []
            if 'hmrc_results' in st.session_state:
                del st.session_state.hmrc_results
            st.rerun()

# Step 3: Monitor Progress (for PDF files)
elif st.session_state.processing_started and st.session_state.current_job_id:
    # Check if we have multiple PDF jobs
    pdf_job_ids = st.session_state.get('pdf_job_ids', [st.session_state.current_job_id])
    
    st.header("📊 Processing Progress")
    
    if len(pdf_job_ids) > 1:
        st.info(f"📄 Processing **{len(pdf_job_ids)} PDF files** together")
    
    # Get progress for all jobs
    all_progress = []
    all_completed = True
    total_pages_all = 0
    processed_pages_all = 0
    
    for job_id in pdf_job_ids:
        progress_data = processor.get_job_progress(job_id)
        all_progress.append((job_id, progress_data))
        
        if progress_data['status'] != 'completed':
            all_completed = False
        
        total_pages_all += progress_data['total_pages']
        processed_pages_all += progress_data['pages_processed']
    
    # Combined progress bar
    combined_progress = (processed_pages_all / total_pages_all * 100) if total_pages_all > 0 else 0
    st.progress(combined_progress / 100, text=f"**{combined_progress:.1f}%** complete ({processed_pages_all}/{total_pages_all} pages)")
    
    # Show progress for each PDF
    for idx, (job_id, progress_data) in enumerate(all_progress, 1):
        status = progress_data['status']
        filename = processor.get_job_metadata(job_id).get('pdf_path', '').split('/')[-1]
        
        with st.expander(f"{'✅' if status == 'completed' else '⏳'} PDF {idx}: {filename} ({progress_data['pages_processed']}/{progress_data['total_pages']} pages)", 
                        expanded=(len(pdf_job_ids) == 1)):
            
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Status", status.upper())
            with col2:
                success_count = sum(1 for p in progress_data['pages'] if p['status'] == 'success')
                st.metric("Successful", success_count)
            with col3:
                failed_count = sum(1 for p in progress_data['pages'] if p['status'] == 'OCR_FAILED')
                st.metric("Failed", failed_count)
            
            # Page details
            if progress_data['pages']:
                pages_df_data = []
                for page in progress_data['pages']:
                    pages_df_data.append({
                        "Page": page['page_number'],
                        "Status": "✅" if page['status'] == 'success' else "❌",
                        "Method": page.get('method', 'N/A'),
                        "Chars": len(page.get('text', ''))
                    })
                
                df = pd.DataFrame(pages_df_data)
                st.dataframe(df, use_container_width=True, hide_index=True, height=200)
    
    st.caption(f"🕐 Last updated: {datetime.now().strftime('%H:%M:%S')}")
    
    # Manual refresh button
    if not all_completed:
        col1, col2 = st.columns([3, 1])
        with col1:
            st.info("🔄 **Auto-refreshing every 2 seconds...**")
        with col2:
            if st.button("🔄 Manual Refresh", key="refresh"):
                st.rerun()
    
    elif all_completed:
        st.success(f"✅ **Text Extraction Complete for all {len(pdf_job_ids)} PDF(s)!**")
        
        # Combined summary statistics
        total_pages = sum(p[1]['total_pages'] for p in all_progress)
        successful = sum(sum(1 for pg in p[1]['pages'] if pg['status'] == 'success') for p in all_progress)
        failed = sum(sum(1 for pg in p[1]['pages'] if pg['status'] == 'OCR_FAILED') for p in all_progress)
        total_chars = sum(sum(len(pg.get('text', '')) for pg in p[1]['pages']) for p in all_progress)
        
        success_pct = (successful/total_pages*100) if total_pages > 0 else 0
        st.info(f"""
        **Combined Extraction Summary:**
        - PDF Files: {len(pdf_job_ids)}
        - Total Pages: {total_pages}
        - Successful: {successful} ({success_pct:.1f}%)
        - Failed: {failed}
        - Total Characters: {total_chars:,}
        """)
        
        st.divider()
        
        # PASS 2: Line Item Parsing (for all PDFs together)
        st.header("📋 Step 2: Parse Line Items from All PDFs")
        
        # Check if we've already parsed (any job in the list)
        if 'parsed_items' not in st.session_state or st.session_state.get('parsed_job_ids') != pdf_job_ids:
            st.session_state.parsed_items = None
            st.session_state.parsed_job_ids = None
        
        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("🔍 Parse All Line Items", type="primary", use_container_width=True):
                with st.spinner(f"Parsing line items from {len(pdf_job_ids)} PDF file(s)..."):
                    all_items = []
                    errors = []
                    
                    # Parse each PDF and combine items
                    for idx, job_id in enumerate(pdf_job_ids, 1):
                        job_dir = processor.get_job_dir(job_id)
                        metadata = processor.get_job_metadata(job_id)
                        direction = metadata.get('direction', 'export')
                        filename = metadata.get('pdf_path', '').split('/')[-1]
                        
                        try:
                            result = parser.parse_job_items(job_dir, direction)
                            
                            if 'error' in result:
                                errors.append(f"PDF {idx} ({filename}): {result['error']}")
                            else:
                                items = result.get('items', [])
                                fmt = result.get('format_type', 'unknown')
                                all_items.extend(items)
                                if items:
                                    st.success(f"✅ PDF {idx} ({filename}): {len(items)} items parsed")
                                else:
                                    st.warning(f"⚠️ PDF {idx} ({filename}): No items found")
                        except Exception as e:
                            errors.append(f"PDF {idx} ({filename}): Exception - {str(e)}")
                            fmt = 'error'
                    
                    # Show errors if any
                    if errors:
                        st.error("⚠️ **Parsing Errors:**")
                        for err in errors:
                            st.text(f"  • {err}")
                    
                    # Store combined results
                    st.session_state.parsed_items = {'items': all_items, 'format_type': fmt}
                    st.session_state.parsed_job_ids = pdf_job_ids
                    st.session_state.pop('hs_validation_results', None)
                    st.rerun()
        
        with col2:
            # Download combined pages.json for all PDFs
            combined_data = {}
            for job_id in pdf_job_ids:
                pages_json_path = processor.get_job_dir(job_id) / "pages.json"
                if pages_json_path.exists():
                    with open(pages_json_path, 'r') as f:
                        combined_data[job_id] = json.load(f)
            
            if combined_data:
                st.download_button(
                    "📥 Download All Pages JSON",
                    data=json.dumps(combined_data, indent=2),
                    file_name=f"combined_pages_{len(pdf_job_ids)}_files.json",
                    mime="application/json",
                    use_container_width=True
                )
        
        with col3:
            if st.button("🔄 Start New Job", use_container_width=True):
                st.session_state.processing_started = False
                st.session_state.current_job_id = None
                st.session_state.pdf_job_ids = []
                st.session_state.parsed_items = None
                st.session_state.line_items = []
                st.session_state.non_pdf_processed = False
                st.session_state.pop('hs_validation_results', None)
                st.rerun()
        
        # Add debug view for extracted text
        with st.expander("🔍 Debug: View Extracted Text from All PDFs"):
            for idx, job_id in enumerate(pdf_job_ids, 1):
                pages_json_path = processor.get_job_dir(job_id) / "pages.json"
                if pages_json_path.exists():
                    with open(pages_json_path, 'r') as f:
                        pages_data = json.load(f)
                    
                    filename = processor.get_job_metadata(job_id).get('pdf_path', '').split('/')[-1]
                    all_text = ""
                    for page in pages_data.get("pages", []):
                        if page.get("status") == "success":
                            all_text += f"\n--- PAGE {page.get('page_number')} ---\n{page.get('text', '')}\n"
                    
                    st.subheader(f"PDF {idx}: {filename}")
                    st.text_area(
                        f"Extracted text ({len(all_text)} chars)",
                        value=all_text[:5000] + ("..." if len(all_text) > 5000 else ""),
                        height=200,
                        key=f"debug_text_{job_id}"
                    )
        
        # Show parsed items if available
        if st.session_state.parsed_items:
            st.divider()
            result = st.session_state.parsed_items
            
            if 'error' in result:
                st.error(f"❌ {result['error']}")
            else:
                items = result.get('items', [])
                
                # Combine with any non-PDF items if they exist
                non_pdf_items = st.session_state.get('line_items', [])
                if non_pdf_items:
                    items = items + non_pdf_items
                    st.info(f"📂 Combined items from PDFs and Excel/Word files")
                
                st.success(f"✅ **Total: {len(items)} line items parsed**")
                
                # Show format detection feedback
                fmt_type = result.get('format_type', 'unknown')
                fmt_labels = {
                    'vertical_table': '📋 Vertical table (e.g. ATI-style multi-page invoices)',
                    'horizontal_table': '📋 Horizontal table (standard row-per-item invoices)',
                    'pattern': '🔍 Pattern-based (free-form text layout)',
                    'unknown': '❓ Unknown format'
                }
                fmt_label = fmt_labels.get(fmt_type, fmt_labels['unknown'])
                st.caption(f"Format detected: {fmt_label}")
                
                if len(items) == 0:
                    st.warning(
                        "⚠️ **No items could be extracted.** This may mean the invoice layout "
                        "is not yet supported. Please send this PDF to the tool maintainer so "
                        "support can be added."
                    )

                # Auto-validate HS codes against HMRC Trade Tariff
                hs_validation_key = 'hs_validation_results'
                if hs_validation_key not in st.session_state:
                    unique_codes = list(set(
                        it.get('commodity_code', '') for it in items
                        if it.get('commodity_code')
                    ))
                    if unique_codes:
                        with st.spinner(f"Validating {len(unique_codes)} HS codes against HMRC tariff..."):
                            try:
                                validation = hmrc_api.validate_commodity_codes(
                                    unique_codes, direction=direction.lower()
                                )
                                st.session_state[hs_validation_key] = validation
                            except Exception:
                                st.session_state[hs_validation_key] = {}

                hs_validation = st.session_state.get(hs_validation_key, {})

                # ---------------------------------------------------------
                # Helper: score how well an item description matches a
                # tariff candidate description.  Returns 0.0 – 1.0.
                # ---------------------------------------------------------
                def _desc_match_score(item_desc: str, tariff_desc: str) -> float:
                    """Word-overlap score between an invoice description and a tariff candidate."""
                    import re as _re
                    stop = {'for', 'of', 'or', 'and', 'the', 'a', 'an', 'in',
                            'to', 'with', 'not', 'more', 'than', 'other', 'use',
                            'but', 'on', 'by', 'at', 'from', 'no', 'its', 'is'}
                    def _tokens(s):
                        # Strip HTML tags then tokenise
                        s = _re.sub(r'<[^>]+>', ' ', s)
                        return {w for w in _re.findall(r'[a-z]{2,}', s.lower()) if w not in stop}
                    a = _tokens(item_desc)
                    b = _tokens(tariff_desc)
                    if not a or not b:
                        return 0.0
                    overlap = a & b
                    # Jaccard-like but weighted toward the smaller set
                    return len(overlap) / min(len(a), len(b)) if min(len(a), len(b)) > 0 else 0.0

                # ---------------------------------------------------------
                # Split invalid codes into auto-fixable vs manual-review
                # ---------------------------------------------------------
                invalid_codes = {c: v for c, v in hs_validation.items()
                                 if not v.get('valid', True)}
                auto_fixed: dict[str, str] = {}   # old_code -> new_code
                auto_fixed_desc: dict[str, str] = {}  # old_code -> tariff desc
                still_invalid: dict[str, dict] = {}

                is_export = direction.lower() == "export"

                for code, result in invalid_codes.items():
                    resolved = result.get('resolved_code')
                    if resolved:
                        new_code = resolved[:8] if is_export else resolved
                        auto_fixed[code] = new_code
                        auto_fixed_desc[code] = result.get('description', '')
                    else:
                        still_invalid[code] = result

                # ---------------------------------------------------------
                # Description-based matching for multi-candidate codes
                # ---------------------------------------------------------
                desc_resolved: dict[str, str] = {}   # old_code -> new_code (per-item)
                desc_resolved_info: dict[str, list] = {}  # old_code -> [(item_desc, new_code, tariff_desc, score)]

                for code, result in list(still_invalid.items()):
                    candidates = result.get('candidates', [])
                    if not candidates:
                        continue

                    # Gather all item descriptions using this code
                    item_descs = [
                        it.get('description', '')
                        for it in items if it.get('commodity_code') == code
                    ]
                    if not item_descs:
                        continue

                    # For each item, score all candidates
                    all_matched = True
                    matches = []
                    for idesc in item_descs:
                        scores = [
                            (_desc_match_score(idesc, c['description']), c)
                            for c in candidates
                        ]
                        scores.sort(key=lambda x: x[0], reverse=True)
                        best_score, best_cand = scores[0]

                        if best_score >= 0.25:
                            new_code = best_cand['code'][:8] if is_export else best_cand['code']
                            matches.append((idesc, new_code, best_cand['description'], best_score))
                        else:
                            # No good match — try to pick the "Other" catch-all
                            other_cands = [
                                c for c in candidates
                                if c['description'].strip().lower() == 'other'
                            ]
                            if other_cands:
                                oc = other_cands[-1]  # last "Other" is typically broadest
                                new_code = oc['code'][:8] if is_export else oc['code']
                                matches.append((idesc, new_code, 'Other (fallback)', 0.0))
                            else:
                                all_matched = False
                                matches.append((idesc, None, None, 0.0))

                    if all_matched and matches:
                        # All items with this code were matched
                        desc_resolved_info[code] = matches
                        # Use the first match's code as the resolved code
                        # (items may get different codes if descriptions differ)
                        del still_invalid[code]

                # Apply description-based corrections to items
                if desc_resolved_info:
                    for code, match_list in desc_resolved_info.items():
                        # Build a map of item_desc -> new_code for per-item assignment
                        idx = 0
                        for item in items:
                            if item.get('commodity_code') != code:
                                continue
                            if idx < len(match_list):
                                _, new_code, tdesc, score = match_list[idx]
                                idx += 1
                                if new_code:
                                    old = item['commodity_code']
                                    item['commodity_code'] = new_code
                                    existing = item.get('review_notes', '')
                                    note = f"HS auto-classified: {old} → {new_code} ({tdesc[:50]}, score={score:.2f})"
                                    if note not in (existing or ''):
                                        item['review_notes'] = (
                                            f"{existing}; {note}" if existing else note
                                        )
                                    auto_fixed[old] = new_code
                                    auto_fixed_desc[old] = tdesc or ''

                # Apply auto-corrections to items (single-resolved codes)
                if auto_fixed:
                    for item in items:
                        cc = item.get('commodity_code', '')
                        if cc in auto_fixed:
                            old = cc
                            item['commodity_code'] = auto_fixed[cc]
                            existing = item.get('review_notes', '')
                            note = f"HS code auto-corrected: {old} → {auto_fixed[cc]}"
                            if note not in (existing or ''):
                                item['review_notes'] = (
                                    f"{existing}; {note}" if existing else note
                                )
                    st.warning(
                        f"🔄 **{len(auto_fixed)} HS code(s) auto-corrected:**"
                    )
                    with st.expander("Auto-correction Details", expanded=True):
                        for old_c, new_c in sorted(auto_fixed.items()):
                            desc = auto_fixed_desc.get(old_c, '')
                            desc_short = f" — {desc[:60]}" if desc else ""
                            st.markdown(f"- `{old_c}` → `{new_c}`{desc_short}")

                # Flag remaining unfixable codes for manual review
                if still_invalid:
                    for item in items:
                        cc = item.get('commodity_code', '')
                        if cc in still_invalid:
                            item['needs_review'] = True
                            existing = item.get('review_notes', '')
                            msg = still_invalid[cc].get('error', 'Invalid HS code')
                            if msg not in (existing or ''):
                                item['review_notes'] = (
                                    f"{existing}; {msg}" if existing else msg
                                )
                    st.error(
                        f"❌ **{len(still_invalid)} HS code(s) could not be resolved — manual review needed:** "
                        + ", ".join(f"`{c}`" for c in sorted(still_invalid))
                    )
                    with st.expander("Unresolved HS Code Details"):
                        for c, v in sorted(still_invalid.items()):
                            st.markdown(f"- **{c}**: {v.get('error', 'Invalid')}")

                if not invalid_codes and hs_validation:
                    st.success(f"✅ All {len(hs_validation)} HS codes validated against HMRC tariff")

                
                # Show items needing review with detailed information
                needs_review = [item for item in items if item.get('needs_review')]
                if needs_review:
                    st.warning(f"⚠️ **{len(needs_review)} items need review** (low confidence or HMRC validation issues)")
                    
                    # Show expandable details for review items
                    with st.expander("📋 View Items Needing Review", expanded=False):
                        for item in needs_review:
                            item_num = item.get('item_number', 'N/A')
                            page = item.get('pages', [1])[0]
                            hs_code = item.get('commodity_code', 'N/A')
                            desc = item.get('description', 'N/A')[:50]
                            notes = item.get('review_notes', 'Low confidence')
                            
                            st.markdown(f"""
                            **Item #{item_num}** (Page {page}) - HS Code: `{hs_code}`  
                            Description: {desc}...  
                            ⚠️ *{notes}*
                            """)
                            st.divider()
                
                # Consolidation toggle
                st.subheader("📊 Parsed Items")
                consolidation_mode = st.radio(
                    "Consolidation mode",
                    options=["HS Code + Country of Origin", "HS Code only", "No consolidation"],
                    index=0,
                    horizontal=True,
                    help="Choose how to group items into declaration lines",
                    key="pdf_consolidation_mode"
                )
                consolidate = (consolidation_mode != "No consolidation")
                split_by_country = (consolidation_mode == "HS Code + Country of Origin")
                
                # Convert to DataFrame for display
                if items:
                    # Consolidate if requested
                    if consolidate:
                        # Group by commodity code (and optionally country of origin)
                        grouped = group_by_commodity_code(items, group_by_origin=split_by_country)
                        
                        # Create consolidated items with proper summing
                        consolidated_items = []
                        for code_key, group_items in grouped.items():
                            from consolidation import consolidate_items
                            consolidated = consolidate_items(group_items)
                            
                            # Handle composite key "HS_CODE|COUNTRY" from group_by_origin
                            if code_key.startswith('__BLANK_'):
                                display_code = ''
                            elif '|' in code_key:
                                display_code = code_key.split('|')[0]
                            else:
                                display_code = code_key
                            
                            # Format for display
                            consolidated_items.append({
                                'commodity_code': display_code,
                                'description': f"{consolidated['description']} ({consolidated['item_count']} items)" if consolidated['item_count'] > 1 else consolidated['description'],
                                'quantity': str(int(consolidated['total_quantity'])) if consolidated['total_quantity'] else '',
                                'uom': group_items[0].get('uom', 'pcs'),
                                'total_value': f"{consolidated['total_value']:.2f}" if consolidated['total_value'] else '',
                                'currency': group_items[0].get('currency', 'GBP'),
                                'net_weight': f"{consolidated['total_net_weight']:.3f}" if consolidated['total_net_weight'] else '',
                                'country_of_origin': ', '.join(consolidated['countries_of_origin']),
                                'item_count': consolidated['item_count']
                            })
                        
                        df_items = pd.DataFrame(consolidated_items)
                        label = "HS code + country of origin" if split_by_country else "HS code"
                        st.info(f"📦 **Consolidated {len(items)} items into {len(consolidated_items)} declaration lines** (grouped by {label})")
                        
                        # Consolidated view: read-only
                        final_cols = ['commodity_code', 'description', 'quantity', 'uom',
                                      'total_value', 'currency', 'net_weight', 'country_of_origin']
                        df_display = df_items[[c for c in final_cols if c in df_items.columns]]
                        st.dataframe(df_display, use_container_width=True, height=400)
                    else:
                        df_items = pd.DataFrame(items)
                        
                        # Add visual confidence indicator column
                        def _conf_icon(row):
                            if row.get('needs_review', False):
                                return '🟡'
                            conf = row.get('confidence', 0)
                            if conf >= 0.8:
                                return '🟢'
                            elif conf >= 0.6:
                                return '🔵'
                            return '🔴'
                        df_items['status'] = df_items.apply(_conf_icon, axis=1)
                        
                        # Reorder columns (status first for visibility)
                        cols_order = ['status', 'item_number', 'description', 'commodity_code', 'quantity', 'uom', 
                                    'unit_value', 'total_value', 'currency', 'net_weight', 'country_of_origin', 
                                    'confidence', 'needs_review', 'review_notes', 'pages']
                        
                        display_cols = [c for c in cols_order if c in df_items.columns]
                        df_display = df_items[display_cols].copy()
                        
                        # Editable columns (user can correct these)
                        editable_cols = {'description', 'commodity_code', 'quantity', 'uom',
                                         'unit_value', 'total_value', 'net_weight', 'country_of_origin'}
                        
                        # Build column config: editable columns get editing enabled,
                        # all others are disabled (read-only)
                        col_config = {}
                        for col in display_cols:
                            if col == 'status':
                                col_config[col] = st.column_config.TextColumn("", disabled=True, width="small")
                            elif col in editable_cols:
                                col_config[col] = st.column_config.TextColumn(col.replace('_', ' ').title())
                            else:
                                col_config[col] = st.column_config.Column(col.replace('_', ' ').title(), disabled=True)
                        
                        st.caption("✏️ **Click any white cell to edit.** Grey cells are read-only. Changes are used in the export.")
                        edited_df = st.data_editor(
                            df_display,
                            use_container_width=True,
                            height=400,
                            num_rows="fixed",
                            column_config=col_config,
                            key="item_editor"
                        )
                        st.caption("🟢 High confidence (≥0.8) | 🔵 Medium (≥0.6) | 🔴 Low (<0.6) | 🟡 Needs review — see Review Notes column")
                        
                        # Sync edits back into the items list so export picks them up
                        for i, row in edited_df.iterrows():
                            if i < len(items):
                                for col in editable_cols:
                                    if col in row.index:
                                        val = row[col]
                                        # Convert NaN/None to empty string
                                        if pd.isna(val):
                                            val = ''
                                        items[i][col] = val
                    
                    # Display CDS Metadata (Incoterms, Package Info, etc.)
                    if st.session_state.get('invoice_metadata'):
                        st.divider()
                        st.subheader("📦 CDS Declaration Metadata")
                        invoice_meta = st.session_state.invoice_metadata
                        
                        col1, col2, col3, col4 = st.columns(4)
                        with col1:
                            incoterm = invoice_meta.get('incoterm', 'Not found')
                            st.metric("Incoterm", incoterm if incoterm else "Not found")
                        with col2:
                            cpc = invoice_meta.get('cpc_code', '4000')
                            st.metric("CPC Code", cpc, help="Customs Procedure Code")
                        with col3:
                            val_method = invoice_meta.get('valuation_method', '1')
                            st.metric("Valuation Method", f"Method{val_method}", help="1 = Transaction Value")
                        with col4:
                            packages = invoice_meta.get('number_of_packages', 'Not found')
                            pkg_type = invoice_meta.get('package_type', '')
                            if packages and packages != 'Not found':
                                st.metric("Packages", f"{packages} {pkg_type if pkg_type else ''}")
                            else:
                                st.metric("Packages", "Not found")
                        
                        col5, col6, col7 = st.columns(3)
                        with col5:
                            total_value = invoice_meta.get('total_invoice_value')
                            if total_value:
                                curr = invoice_meta.get('currency', 'GBP')
                                st.metric("Invoice Total", f"{curr} {total_value:,.2f}")
                        with col6:
                            net_wt = invoice_meta.get('total_net_weight')
                            if net_wt:
                                st.metric("Total Net Weight", f"{net_wt:.3f} kg")
                        with col7:
                            gross_wt = invoice_meta.get('total_gross_weight')
                            if gross_wt:
                                st.metric("Total Gross Weight", f"{gross_wt:.3f} kg")
                    
                    # Step 3: HMRC Enrichment and Export
                    st.divider()
                    st.subheader("🔍 Step 3: HMRC Enrichment & Export")
                    
                    # Get direction to set appropriate defaults
                    metadata = processor.get_job_metadata(job_id)
                    current_direction = metadata.get('direction', 'export')
                    
                    # HMRC Lookup options - different for import vs export
                    if current_direction.lower() == 'export':
                        export_only_measures = st.checkbox("📤 Export measures only", value=True, 
                            help="Filter to show only export-related measures (Export control, Export authorization)")
                    else:
                        # For imports, always show all relevant import measures
                        st.info("💡 Import mode: Will show duties, VAT, suspensions, and restrictions relevant to your origin country")
                        export_only_measures = False
                    
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        if st.button("🔍 Lookup HMRC Data", type="primary", use_container_width=True):
                            metadata = processor.get_job_metadata(job_id)
                            direction = metadata.get('direction', 'export')
                            dest_country = metadata.get('country', '')
                            
                            # Map country name to ISO code via HMRC-sourced lookup
                            dest_country_code = COUNTRY_TO_ISO.get(dest_country, dest_country[:2].upper() if dest_country else None)
                            
                            with st.spinner("Looking up commodity codes in HMRC API..."):
                                # Get unique codes (df_items has commodity_code column regardless of consolidation)
                                codes = df_items['commodity_code'].unique()
                                
                                hmrc_results = {}
                                
                                progress_bar = st.progress(0)
                                for idx, code in enumerate(codes):
                                    data = hmrc_api.get_commodity_details(
                                        code, 
                                        direction=direction,
                                        destination_country=dest_country_code,
                                        export_only=export_only_measures
                                    )
                                    hmrc_results[code] = data
                                    progress_bar.progress((idx + 1) / len(codes))
                                
                                st.session_state.hmrc_results = hmrc_results
                                st.session_state.hmrc_consolidate_state = consolidate
                                st.session_state.hmrc_dest_country = dest_country
                                st.rerun()
                    
                    with col2:
                        if 'hmrc_results' in st.session_state and st.session_state.hmrc_results:
                            if st.button("🔄 Clear & Re-lookup", use_container_width=True, help="Clear cached results and lookup again"):
                                del st.session_state.hmrc_results
                                if 'hmrc_consolidate_state' in st.session_state:
                                    del st.session_state.hmrc_consolidate_state
                                if 'hmrc_dest_country' in st.session_state:
                                    del st.session_state.hmrc_dest_country
                                st.success("Cache cleared! Click 'Lookup HMRC Data' again.")
                                st.rerun()
                        
                        if st.button("📥 Export to Excel", use_container_width=True):
                            metadata = processor.get_job_metadata(job_id)
                            
                            # Get HMRC data if available
                            hmrc_results = st.session_state.get('hmrc_results', None)
                            
                            # Get invoice metadata if available
                            invoice_metadata = st.session_state.get('invoice_metadata', {})
                            
                            # Create comprehensive Excel export
                            excel_bytes = create_comprehensive_export(
                                items=items,
                                hmrc_data=hmrc_results,
                                direction=metadata.get('direction', 'export'),
                                country=metadata.get('country', ''),
                                consolidate=consolidate,
                                metadata=invoice_metadata
                            )
                            
                            st.download_button(
                                "📥 Download Excel File",
                                data=excel_bytes.getvalue(),
                                file_name=f"CDS-Customs-Entry-Worksheet-{job_id}.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                use_container_width=True
                            )

                        if st.button("📋 FCL Excel Sheet", use_container_width=True,
                                     help="Export in CDS Customs Entry Worksheet format using the same consolidation as above."):
                            metadata = processor.get_job_metadata(job_id)
                            hmrc_results = st.session_state.get('hmrc_results', None)
                            invoice_metadata = st.session_state.get('invoice_metadata', {})
                            
                            # Export respects the consolidation choice:
                            # - If consolidated: pass consolidated df with consolidate=False
                            # - If not consolidated: pass raw items with consolidate=True (so FCL consolidates by HS code)
                            if consolidate:
                                # Consolidated display → pass df rows with consolidate=False
                                export_items = df_items.to_dict('records')
                                should_consolidate = False
                            else:
                                # Individual items display → pass raw items with consolidate=True
                                export_items = items
                                should_consolidate = True
                            
                            cds_bytes = create_cds_excel(
                                items=export_items,
                                direction=metadata.get('direction', 'export'),
                                hmrc_data=hmrc_results,
                                consolidate=should_consolidate,
                                metadata=invoice_metadata
                            )
                            st.download_button(
                                "📋 Download FCL Excel Sheet",
                                data=cds_bytes.getvalue(),
                                file_name=f"CDS-Customs-Entry-Worksheet-FCL-{job_id}.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                use_container_width=True
                            )
                    
                    # JSON export in separate row
                    st.markdown("")
                    if st.button("📥 Download JSON", use_container_width=False):
                        import json
                        json_data = json.dumps(result, indent=2)
                        st.download_button(
                            "⬇️ Get JSON File",
                            data=json_data,
                            file_name=f"{job_id}_parsed_items.json",
                            mime="application/json"
                        )
                    
                    # Show HMRC results if available
                    if 'hmrc_results' in st.session_state and st.session_state.hmrc_results:
                        st.divider()
                        st.subheader("📋 HMRC Tariff Information")
                        st.info("💡 **Tip:** Use 'Export to Excel' button above to download all data together including document codes in separate columns")
                        
                        # Show consolidated or individual based on lookup state
                        was_consolidated = st.session_state.get('hmrc_consolidate_state', False)
                        dest_country = st.session_state.get('hmrc_dest_country', '')
                        if was_consolidated:
                            st.info(f"📦 Showing {len(st.session_state.hmrc_results)} consolidated commodity codes")
                        if dest_country:
                            st.info(f"🌍 Filtered for destination: {dest_country}")
                        
                        for code, data in st.session_state.hmrc_results.items():
                            with st.expander(f"📦 {code} - {data.get('description', 'N/A')[:80]}..."):
                                if 'error' in data:
                                    st.error(f"❌ {data['error']}")
                                else:
                                    # Get direction to show appropriate info
                                    direction = processor.get_job_metadata(job_id).get('direction', 'export')
                                    
                                    # IMPORT MODE: Prominent display of key tariff info
                                    if direction.lower() == 'import':
                                        # Top section: Duty and VAT (most important!)
                                        third_duty = data.get('third_country_duty')
                                        vat = data.get('vat_rate')
                                        
                                        # Show duty/VAT in prominent metric boxes
                                        metric_cols = st.columns(3)
                                        with metric_cols[0]:
                                            if third_duty:
                                                # Handle masked/complex duty rates
                                                if third_duty == '****':
                                                    st.metric("💷 Standard Import Duty", "Complex rate", help="The duty rate varies based on conditions. See suspensions below for potential 0% options.")
                                                else:
                                                    st.metric("💷 Standard Import Duty", third_duty, help="Third Country Duty (MFN rate)")
                                            else:
                                                st.metric("💷 Import Duty", "Not found", help="Duty rate not available in HMRC data")
                                        with metric_cols[1]:
                                            if vat:
                                                # Handle masked VAT
                                                if vat == '****':
                                                    st.metric("💰 VAT Rate", "Standard: 20%", help="Most goods are subject to standard VAT rate")
                                                else:
                                                    st.metric("💰 VAT Rate", vat, help="Value Added Tax")
                                            else:
                                                st.metric("💰 VAT Rate", "Not found", help="VAT rate not available in HMRC data")
                                        with metric_cols[2]:
                                            susp_count = len(data.get('suspensions', []))
                                            if susp_count > 0:
                                                st.metric("🎁 Duty Suspensions", f"{susp_count} available", delta="Money Saver!", help="Temporary 0% duty available")
                                            else:
                                                st.metric("🎁 Duty Suspensions", "None")
                                        
                                        # Debug: Show what values were extracted
                                        if not third_duty and not vat:
                                            with st.expander("🔍 Debug: Why no duty/VAT?"):
                                                st.caption(f"third_country_duty value: `{repr(third_duty)}`")
                                                st.caption(f"vat_rate value: `{repr(vat)}`")
                                                st.caption(f"Measures kept: {data.get('_debug_total_measures', 0) - data.get('_debug_filtered_count', 0)}")
                                                st.caption("Likely cause: HMRC API returned empty duty_expression field")
                                                
                                        st.divider()
                                    
                                    # Two column layout for details
                                    col1, col2 = st.columns(2)
                                    with col1:
                                        st.write("**Description:**")
                                        st.write(data.get('description', 'N/A'))
                                        
                                        st.write("**📏 Supplementary Units:**")
                                        supp = data.get('supplementary_units', 'Not required')
                                        st.info(f"{supp}" if supp != 'Not required' else "None required")
                                        
                                        # Export mode: Show duty rates here (not at top)
                                        if direction.lower() != 'import':
                                            third_duty = data.get('third_country_duty')
                                            pref_duties = data.get('preferential_duty', {})
                                            
                                            if third_duty and not pref_duties:
                                                st.write("**💷 Import Duty:**")
                                                st.write(f"{third_duty}")
                                            elif third_duty and pref_duties:
                                                st.write("**💷 Import Duty Rates:**")
                                                st.write(f"• Standard (Third Country): {third_duty}")
                                                for country, rate in pref_duties.items():
                                                    st.success(f"• {country}: {rate} ✓ Preferential")
                                            
                                            # Show VAT rate
                                            vat = data.get('vat_rate')
                                            if vat:
                                                st.write("**💰 VAT Rate:**")
                                                st.write(vat)
                                        
                                        # Show suspensions (money savers!) - Enhanced display
                                        suspensions = data.get('suspensions', [])
                                        if suspensions:
                                            st.success("### 🎁 Duty Suspensions Available")
                                            st.caption("These measures allow 0% import duty under certain conditions:")
                                            for idx, susp in enumerate(suspensions, 1):
                                                with st.container():
                                                    st.markdown(f"**{idx}. {susp.get('type', 'N/A')}**")
                                                    duty_rate = susp.get('duty', '0%')
                                                    st.markdown(f"   💰 Duty Rate: **{duty_rate}**")
                                                    if susp.get('conditions'):
                                                        st.caption(f"   📋 Conditions:")
                                                        for cond in susp['conditions'][:3]:
                                                            st.caption(f"      • {cond}")
                                        
                                        # Show quotas
                                        quotas = data.get('quotas', [])
                                        if quotas:
                                            st.write("**📊 Tariff Rate Quotas:**")
                                            for quota in quotas:
                                                st.info(f"• {quota.get('type', 'N/A')}: {quota.get('duty', 'N/A')}")
                                                if quota.get('geographical_area') and quota['geographical_area'] != 'ERGA OMNES (All countries)':
                                                    st.caption(f"  Applied to: {quota['geographical_area']}")
                                        
                                        # Show document codes with group selection
                                        groups = data.get('document_code_groups', [])
                                        selected = data.get('selected_document_codes', {})
                                        doc_codes = data.get('document_codes', {})

                                        if groups:
                                            st.write("**📄 Document Codes:**")
                                            for gi, grp in enumerate(groups):
                                                codes_in_group = grp.get('codes', [])
                                                if len(codes_in_group) <= 1:
                                                    c = codes_in_group[0]
                                                    st.write(f"- ✅ `{c['code']}`: {c['requirement'][:80]}")
                                                else:
                                                    options = [c['code'] for c in codes_in_group]
                                                    labels = [f"{c['code']} — {c['requirement'][:60]}" for c in codes_in_group]
                                                    default_idx = 0
                                                    for idx_c, c in enumerate(codes_in_group):
                                                        if c['code'] in selected:
                                                            default_idx = idx_c
                                                            break
                                                    chosen = st.selectbox(
                                                        grp.get('measure', 'Document Code')[:50],
                                                        labels,
                                                        index=default_idx,
                                                        key=f"pdf_dc_{code}_{gi}"
                                                    )
                                                    chosen_code = options[labels.index(chosen)]
                                                    chosen_req = codes_in_group[labels.index(chosen)]['requirement']
                                                    if 'hmrc_results' in st.session_state:
                                                        st.session_state.hmrc_results[code]['selected_document_codes'][chosen_code] = chosen_req
                                        elif doc_codes:
                                            st.write("**📄 Document Codes Required:**")
                                            for doc_code, requirement in doc_codes.items():
                                                st.write(f"- `{doc_code}`: {requirement}")
                                        
                                    with col2:
                                        # Show prohibitions/restrictions (warnings!) - Enhanced display
                                        prohibitions = data.get('prohibitions', [])
                                        if prohibitions:
                                            restriction_label = "⚠️ Export Controls" if direction.lower() == 'export' else "⚠️ Import Restrictions"
                                            st.warning(f"### {restriction_label}")
                                            st.caption("These measures require special documentation or conditions:")
                                            for idx, prob in enumerate(prohibitions, 1):
                                                with st.container():
                                                    st.markdown(f"**{idx}. {prob.get('type', 'N/A')}**")
                                                    geo_area = prob.get('geographical_area', '')
                                                    geo_code = prob.get('geo_code', '')
                                                    # Show geographical scope
                                                    if geo_area:
                                                        if geo_area != 'ERGA OMNES (All countries)' and 'erga omnes' not in geo_area.lower():
                                                            st.caption(f"   🌍 Applies to: **{geo_area}** (code: {geo_code})")
                                                        else:
                                                            st.caption(f"   🌍 Applies: **Globally** (all import origins)")
                                                    if prob.get('conditions'):
                                                        st.caption(f"   📋 Requirements:")
                                                        for cond in prob['conditions'][:3]:
                                                            st.caption(f"      • {cond}")
                                        
                                        # Show anti-dumping duties (extra charges!)
                                        anti_dumping = data.get('anti_dumping', [])
                                        if anti_dumping:
                                            st.write("**🚨 Additional Duties:**")
                                            for ad in anti_dumping:
                                                st.error(f"⚠️ {ad.get('type', 'N/A')}")
                                                st.caption(f"   Origin: {ad.get('geographical_area', 'N/A')}")
                                                st.caption(f"   Extra duty: {ad.get('duty', 'N/A')}")
                                        
                                        # Show conditions
                                        conditions = data.get('conditions', [])
                                        if conditions:
                                            conditions_label = "✅ Export Requirements:" if direction.lower() == 'export' else "✅ Import Conditions:"
                                            st.write(f"**{conditions_label}**")
                                            for cond in conditions[:5]:
                                                st.caption(f"• {cond}")
                                        
                                        # Show additional codes
                                        add_codes = data.get('additional_codes', [])
                                        if add_codes:
                                            st.write("**🔢 Additional Codes:**")
                                            seen_codes = set()
                                            for ac in add_codes:
                                                code_key = ac.get('code')
                                                if code_key and code_key not in seen_codes:
                                                    st.caption(f"• {code_key}: {ac.get('description', 'N/A')}")
                                                    seen_codes.add(code_key)
                                                st.warning("📊 Quota restrictions apply")
                                        else:
                                            if data.get('export_licence_required'):
                                                st.warning("⚠️ Export licence may be required")
                                    
                                    # Debug section to see all measure types
                                    debug_measures = data.get('_debug_all_measures', [])
                                    total_measures = data.get('_debug_total_measures', 0)
                                    filtered_count = data.get('_debug_filtered_count', 0)
                                    
                                    # Count categorized items
                                    susp_count = len(data.get('suspensions', []))
                                    quota_count = len(data.get('quotas', []))
                                    ad_count = len(data.get('anti_dumping', []))
                                    prob_count = len(data.get('prohibitions', []))
                                    cond_count = len(data.get('conditions', []))
                                    
                                    if total_measures > 0:
                                        with st.expander(f"🔍 Debug: {total_measures} total, {filtered_count} filtered, {total_measures - filtered_count} kept | Categorized: {susp_count} susp, {quota_count} quota, {ad_count} AD, {prob_count} prohib, {cond_count} cond"):
                                            # Show API call parameters
                                            api_direction = data.get('direction', 'N/A')
                                            api_country = data.get('destination_country', 'None')
                                            st.caption(f"**API Request:** Direction={api_direction}, Country Code={api_country}")
                                            st.caption(f"**User Selection:** {direction.upper()} from/to {dest_country}")
                                            
                                            if susp_count > 0:
                                                st.success(f"✓ Found {susp_count} suspensions")
                                            if quota_count > 0:
                                                st.success(f"✓ Found {quota_count} quotas")
                                            if ad_count > 0:
                                                st.warning(f"⚠️ Found {ad_count} anti-dumping measures")
                                            if prob_count > 0:
                                                st.warning(f"⚠️ Found {prob_count} prohibitions/restrictions")
                                                # Show prohibition details
                                                st.caption("**Prohibition Geo Details:**")
                                                for p in data.get('prohibitions', [])[:3]:
                                                    st.caption(f"  - {p.get('type', 'N/A')[:60]}... → Geo: '{p.get('geographical_area', 'None')}' (code: {p.get('geo_code', 'None')})")
                                            
                                            # Show country filtering debug
                                            country_checks = data.get('_debug_country_checks', [])
                                            direction_checks = data.get('_debug_direction_checks', [])
                                            
                                            if direction_checks:
                                                st.caption("---")
                                                st.error("🔍 DIRECTION Filter Decisions (first 10 - THIS IS WHY NOTHING SHOWS!):")
                                                for i, check in enumerate(direction_checks[:10]):
                                                    kept = "✓ KEPT" if check['matches'] else "✗ FILTERED"
                                                    color = "green" if kept.startswith("✓") else "red"
                                                    st.caption(f"{i+1}. {check['measure']}")
                                                    st.caption(f"   Requested: {check['direction_requested']} | Has 'import': {check['has_import']} | Has 'export': {check['has_export']}")
                                                    st.caption(f"   Export_only mode: {check['export_only']} → **:{color}[{kept}]**")
                                            
                                            if country_checks:
                                                st.caption("---")
                                                st.caption(f"Country Filter Decisions (showing ALL {len(country_checks)} checks):")
                                                # Find Belarus/Russia or show all if less than 15
                                                belarus_russia = [c for c in country_checks if 'belarus' in c.get('geo_desc', '').lower() or 'russia' in c.get('geo_desc', '').lower()]
                                                if belarus_russia:
                                                    st.error("🚨 FOUND BELARUS/RUSSIA MEASURES:")
                                                    for check in belarus_russia:
                                                        decision = check.get('decision', '???')
                                                        color = "green" if decision == 'KEPT' else "red"
                                                        st.caption(f"   {check['measure']}")
                                                        st.caption(f"   Geo: {check['geo_desc']} (code: {check['geo_code']})")
                                                        st.caption(f"   All countries? {check['is_all']} | Selected? {check['is_selected']} → **:{color}[{decision}]**")
                                                        st.caption(f"   Dest country for filter: {check.get('dest', 'N/A')}")
                                                else:
                                                    for i, check in enumerate(country_checks[:15]):
                                                        decision = check.get('decision', '???')
                                                        color = "green" if decision == 'KEPT' else "red"
                                                        st.caption(f"{i+1}. {check['measure']}")
                                                        st.caption(f"   Geo: {check['geo_desc']} (code: {check['geo_code']})")
                                                        st.caption(f"   All countries? {check['is_all']} | Selected? {check['is_selected']} → **:{color}[{decision}]**")
                                            
                                            st.caption("---")
                                            st.caption("All measures from API (before filtering):")
                                            for i, m in enumerate(debug_measures[:30]):  # Show first 30
                                                import_flag = "📥" if m.get('has_import') else ""
                                                export_flag = "📤" if m.get('has_export') else ""
                                                neutral_flag = "⚪" if not m.get('has_import') and not m.get('has_export') else ""
                                                st.text(f"{i+1}. {import_flag}{export_flag}{neutral_flag} {m.get('type', 'N/A')}")
                                                if m.get('duty'):
                                                    st.caption(f"   Duty: {m['duty']}")
                                                if m.get('geo') and m['geo'] != 'ERGA OMNES (All countries)':
                                                    st.caption(f"   Geo: {m['geo']}")
                                    else:
                                        with st.expander("🔍 Debug: No measures found"):
                                            st.error("HMRC API returned no measures for this commodity code!")
                                            st.caption("This could mean:")
                                            st.caption("• The commodity code doesn't exist")
                                            st.caption("• The API had an issue")
                                            st.caption("• The code variant lookup failed")
                else:
                    st.warning(
                        "⚠️ **No items could be parsed from the extracted text.**\n\n"
                        "This usually means the invoice layout isn't recognised yet. "
                        "Try downloading the Pages JSON (above) and sending it along with "
                        "the original PDF to the tool maintainer so support can be added."
                    )
    
    elif status == "error":
        st.error("❌ **Processing Error**")
        metadata = processor.get_job_metadata(job_id)
        error_msg = metadata.get('error', 'Unknown error')
        st.error(f"Error: {error_msg}")
        st.info(
            "💡 **What to do:** Try uploading the file again. If the problem persists, "
            "send the PDF to the tool maintainer with a screenshot of this error."
        )
        
        if st.button("🔄 Start New Job"):
            st.session_state.processing_started = False
            st.session_state.current_job_id = None
            st.rerun()

# Sidebar: Job History
with st.sidebar:
    st.header("📜 Job History")
    
    jobs = processor.list_jobs()
    if jobs:
        st.caption(f"Found {len(jobs)} job(s)")
        
        for job_id in sorted(jobs, reverse=True)[:10]:  # Show last 10
            metadata = processor.get_job_metadata(job_id)
            status = metadata.get('status', 'unknown')
            username_job = metadata.get('username', 'N/A')
            pages = metadata.get('pages_processed', 0)
            total = metadata.get('total_pages', 0)
            
            status_icon = {
                'completed': '✅',
                'processing': '🔄',
                'created': '📝',
                'error': '❌'
            }.get(status, '❓')
            
            with st.expander(f"{status_icon} {job_id[:30]}..."):
                st.text(f"User: {username_job}")
                st.text(f"Status: {status}")
                st.text(f"Progress: {pages}/{total}")
                
                col_load, col_reprocess = st.columns(2)
                with col_load:
                    if st.button(f"Load", key=f"load_{job_id}"):
                        st.session_state.current_job_id = job_id
                        st.session_state.processing_started = True
                        st.rerun()
                with col_reprocess:
                    if status == 'completed' and st.button("🔄 Re-OCR", key=f"reocr_{job_id}", help="Re-run OCR with deskewing on all pages"):
                        def reprocess_bg(jid):
                            try:
                                processor.reprocess_ocr(jid)
                            except Exception as e:
                                processor.update_job_metadata(jid, {"status": "error", "error": str(e)})
                        import threading as _thr
                        _thr.Thread(target=reprocess_bg, args=(job_id,), daemon=True).start()
                        st.session_state.current_job_id = job_id
                        st.session_state.processing_started = True
                        st.rerun()
    else:
        st.info("No jobs yet")

st.divider()
st.caption("💡 **Stable Edition Features:** Page-by-page processing | Resumable jobs | Graceful failure handling | No connection timeouts")



