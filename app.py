"""
Streamlit UI for Job-Based PDF Processing
Stable, resumable, with real-time progress tracking
"""
import streamlit as st
import threading
import time
import json
from pathlib import Path
from datetime import datetime
import pandas as pd
from job_processor import JobProcessor
from line_item_parser import LineItemParser
from consolidation import group_by_commodity_code, create_consolidated_dataframe, export_to_excel
from excel_export import create_comprehensive_export
from hmrc_api import HMRCTariffAPI
from countries import COUNTRIES, COMMON_COUNTRIES
from file_extractor import extract_from_file
import shutil
from streamlit_autorefresh import st_autorefresh

# Version tracking for cache busting
APP_VERSION = "v2.9-parse-measure-components"

st.set_page_config(
    page_title="Customs Invoice Tool - Stable Edition",
    layout="wide",
    page_icon="🚢"
)

# Professional CSS styling
st.markdown("""
<style>
    /* Main styling */
    .stApp {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    }
    
    /* Cards */
    .metric-card {
        background: white;
        padding: 1.5rem;
        border-radius: 10px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        margin: 1rem 0;
    }
    
    /* Progress elements */
    .stProgress > div > div {
        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
    }
    
    /* Headers */
    h1, h2, h3 {
        color: #1e3a8a;
    }
    
    /* Buttons */
    .stButton>button {
        border-radius: 8px;
        font-weight: 600;
        transition: all 0.3s;
    }
    
    .stButton>button:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0,0,0,0.2);
    }
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

st.title("🚢 Customs Invoice Tool - Stable Edition")
st.caption("**Job-based processing with page-by-page extraction - No timeouts!**")

# Version check and cache clear
if 'app_version' not in st.session_state or st.session_state.app_version != APP_VERSION:
    # Clear HMRC cache on version change
    keys_to_clear = ['hmrc_results', 'hmrc_consolidate_state', 'hmrc_dest_country']
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
    # Username captured automatically from system`n    username = st.session_state.username`n    st.info(f" Logged in as: **{username}**")
    
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
        help="Upload multiple PDFs, Excel, or Word files - all will be processed together"
    )

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
    
    # Save uploaded file if new job
    if st.button("🚀 Start Processing", type="primary", disabled=st.session_state.processing_started):
        
        # Process all files
        all_items = []
        pdf_job_ids = []
        
        # First, process non-PDF files (instant)
        if non_pdf_files:
            with st.spinner(f"🔍 Extracting data from {len(non_pdf_files)} Excel/Word file(s)..."):
                for uploaded_file in non_pdf_files:
                    try:
                        text, items, metadata = extract_from_file(uploaded_file, uploaded_file.name, trade_direction=direction)
                        if items:
                            all_items.extend(items)
                            # Store metadata from first file
                            if not st.session_state.invoice_metadata:
                                st.session_state.invoice_metadata = metadata
                            st.success(f"✅ Extracted {len(items)} items from {uploaded_file.name}")
                    except Exception as e:
                        st.error(f"Error processing {uploaded_file.name}: {e}")
        
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
        
        st.session_state.processing_started = True
        st.rerun()

elif not username:
    st.warning("⚠️ Please enter your username")
elif not uploaded_files:
    st.info("� Please upload a file to begin (PDF, Excel, or Word)")

st.divider()

# Display results for non-PDF files (Excel/Word)
if st.session_state.get('non_pdf_processed', False):
    st.header("📊 Extracted Line Items")
    items = st.session_state.get('line_items', [])
    
    if items:
        st.success(f"✅ **Extracted {len(items)} items**")
        
        # Convert to DataFrame and display
        df_items = pd.DataFrame(items)
        
        # Rename total_value to value for display
        if 'total_value' in df_items.columns:
            df_items['value'] = df_items['total_value']
        if 'uom' in df_items.columns:
            df_items['unit'] = df_items['uom']
        
        # Reorder columns
        cols_order = ['description', 'commodity_code', 'quantity', 'unit', 
                    'value', 'country_of_origin', 'net_weight']
        display_cols = [c for c in cols_order if c in df_items.columns]
        df_display = df_items[display_cols].copy()
        
        st.dataframe(df_display, use_container_width=True, height=400)
        
        # Consolidate and export
        st.divider()
        st.subheader("🔍 Consolidation & Export")
        
        col1, col2 = st.columns([3, 1])
        with col1:
            consolidate = st.checkbox("Consolidate by HS Code", value=True)
        
        if consolidate:
            grouped = group_by_commodity_code(items)
            consolidated_items = []
            for code, group_items in grouped.items():
                from consolidation import consolidate_items
                consolidated = consolidate_items(group_items)
                consolidated_items.append({
                    'commodity_code': code,
                    'description': f"{consolidated['description']} ({consolidated['item_count']} items)",
                    'quantity': consolidated['total_quantity'],
                    'value': consolidated['total_value'],
                    'net_weight': consolidated['total_net_weight'],
                    'country_of_origin': ', '.join(consolidated['countries_of_origin'])
                })
            
            df_export = pd.DataFrame(consolidated_items)
            # Reorder columns to match display
            export_cols = ['description', 'commodity_code', 'quantity', 'value', 'country_of_origin', 'net_weight']
            df_export = df_export[[c for c in export_cols if c in df_export.columns]]
            st.info(f"📦 **Consolidated {len(items)} items into {len(consolidated_items)} unique codes**")
        else:
            df_export = df_display
        
        st.dataframe(df_export, use_container_width=True)
        
        # Export button
        col1, col2 = st.columns(2)
        with col1:
            if st.button("📥 Export to Excel", use_container_width=True):
                import json
                excel_path = export_to_excel(
                    df_export,
                    username=st.session_state.username,
                    direction='export',
                    country='',
                    job_id='excel_export'
                )
                
                with open(excel_path, 'rb') as f:
                    st.download_button(
                        "📥 Download Excel",
                        data=f.read(),
                        file_name="customs_declaration.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True
                    )
        
        with col2:
            json_data = json.dumps(items, indent=2)
            st.download_button(
                "📥 Download JSON",
                data=json_data,
                file_name="line_items.json",
                mime="application/json",
                use_container_width=True
            )
        
        # Reset button
        if st.button("🔄 Process Another File", use_container_width=True):
            st.session_state.non_pdf_processed = False
            st.session_state.line_items = []
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
        
        st.info(f"""
        **Combined Extraction Summary:**
        - PDF Files: {len(pdf_job_ids)}
        - Total Pages: {total_pages}
        - Successful: {successful} ({successful/total_pages*100:.1f}%)
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
                                all_items.extend(items)
                                if items:
                                    st.success(f"✅ PDF {idx} ({filename}): {len(items)} items parsed")
                                else:
                                    st.warning(f"⚠️ PDF {idx} ({filename}): No items found")
                        except Exception as e:
                            errors.append(f"PDF {idx} ({filename}): Exception - {str(e)}")
                    
                    # Show errors if any
                    if errors:
                        st.error("⚠️ **Parsing Errors:**")
                        for err in errors:
                            st.text(f"  • {err}")
                    
                    # Store combined results
                    st.session_state.parsed_items = {'items': all_items}
                    st.session_state.parsed_job_ids = pdf_job_ids
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
                
                # Show items needing review
                needs_review = [item for item in items if item.get('needs_review')]
                if needs_review:
                    st.warning(f"⚠️ **{len(needs_review)} items need review** (low confidence or missing data)")
                
                # Consolidation toggle
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.subheader("📊 Parsed Items")
                with col2:
                    consolidate = st.checkbox("Consolidate by HS Code", value=True)
                
                # Convert to DataFrame for display
                if items:
                    # Consolidate if requested
                    if consolidate:
                        # Group by commodity code using consolidation.py
                        grouped = group_by_commodity_code(items)
                        
                        # Create consolidated items with proper summing
                        consolidated_items = []
                        for code, group_items in grouped.items():
                            from consolidation import consolidate_items
                            consolidated = consolidate_items(group_items)
                            
                            # Format for display
                            consolidated_items.append({
                                'commodity_code': code,
                                'description': f"{consolidated['description']} ({consolidated['item_count']} items)",
                                'quantity': str(int(consolidated['total_quantity'])) if consolidated['total_quantity'] else '',
                                'uom': group_items[0].get('uom', 'pcs'),
                                'total_value': f"{consolidated['total_value']:.2f}" if consolidated['total_value'] else '',
                                'currency': group_items[0].get('currency', 'GBP'),
                                'net_weight': f"{consolidated['total_net_weight']:.3f}" if consolidated['total_net_weight'] else '',
                                'country_of_origin': ', '.join(consolidated['countries_of_origin']),
                                'item_count': consolidated['item_count']
                            })
                        
                        df_items = pd.DataFrame(consolidated_items)
                        st.info(f"📦 **Consolidated {len(items)} items into {len(consolidated_items)} unique commodity codes**")
                    else:
                        df_items = pd.DataFrame(items)
                    
                    # Reorder columns
                    cols_order = ['description', 'commodity_code', 'quantity', 'uom', 
                                'unit_value', 'total_value', 'currency', 'net_weight', 'country_of_origin', 
                                'confidence', 'needs_review', 'pages']
                    
                    display_cols = [c for c in cols_order if c in df_items.columns]
                    df_display = df_items[display_cols].copy()
                    
                    # Color code by confidence (if not consolidated)
                    if not consolidate and 'confidence' in df_display.columns:
                        def highlight_confidence(row):
                            if row.get('needs_review', False):
                                return ['background-color: #fff3cd'] * len(row)
                            elif row.get('confidence', 0) >= 0.8:
                                return ['background-color: #d4edda'] * len(row)
                            elif row.get('confidence', 0) >= 0.6:
                                return ['background-color: #d1ecf1'] * len(row)
                            else:
                                return ['background-color: #f8d7da'] * len(row)
                        
                        st.dataframe(
                            df_display.style.apply(highlight_confidence, axis=1),
                            use_container_width=True,
                            height=400
                        )
                        st.caption("🟢 Green: High confidence | 🔵 Blue: Medium confidence | 🟡 Yellow: Needs review | 🔴 Red: Low confidence")
                    else:
                        st.dataframe(
                            df_display,
                            use_container_width=True,
                            height=400
                        )
                    
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
                        st.info("💡 Import mode: Will show duties, VAT, suspensions, preferences, and restrictions relevantto your origin country")
                        export_only_measures = False
                    
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        if st.button("🔍 Lookup HMRC Data", type="primary", use_container_width=True):
                            metadata = processor.get_job_metadata(job_id)
                            direction = metadata.get('direction', 'export')
                            dest_country = metadata.get('country', '')
                            
                            # Map country name to ISO code (basic mapping)
                            country_code_map = {
                                'China': 'CN', 'Germany': 'DE', 'United Kingdom': 'GB',
                                'United States': 'US', 'France': 'FR', 'Italy': 'IT',
                                'Spain': 'ES', 'India': 'IN', 'Japan': 'JP', 'South Korea': 'KR',
                                'Azerbaijan': 'AZ', 'United Arab Emirates': 'AE'
                            }
                            dest_country_code = country_code_map.get(dest_country, dest_country[:2].upper() if dest_country else None)
                            
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
                                file_name=f"{job_id}_customs_declaration.xlsx",
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
                                        pref_duties = data.get('preferential_duty', {})
                                        
                                        # Show duty/VAT in prominent metric boxes
                                        metric_cols = st.columns(4)
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
                                            pref_code = data.get('preference_code', '100')
                                            pref_labels = {
                                                '100': 'MFN',
                                                '115': 'Susp-ATQ',
                                                '119': 'Susp-ATS',
                                                '120': 'Quota',
                                                '200': 'Preferential',
                                                '300': 'GSP'
                                            }
                                            st.metric("🏷️ Preference Code", pref_code, help=f"CDS Preference: {pref_labels.get(pref_code, '')}")
                                        with metric_cols[3]:
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
                                                
                                        # Show preferential rates if available
                                        if pref_duties:
                                            st.success("✅ **Preferential Trade Agreement Rates Available:**")
                                            for country, rate in pref_duties.items():
                                                st.write(f"  • **{country}:** {rate}")
                                        
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
                                        
                                        # Show document codes
                                        doc_codes = data.get('document_codes', {})
                                        if doc_codes:
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
                    st.warning("No items could be parsed from the extracted text")
    
    elif status == "error":
        st.error("❌ **Processing Error**")
        metadata = processor.get_job_metadata(job_id)
        st.error(f"Error: {metadata.get('error', 'Unknown error')}")
        
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
                
                if st.button(f"Load Job", key=f"load_{job_id}"):
                    st.session_state.current_job_id = job_id
                    st.session_state.processing_started = True
                    st.rerun()
    else:
        st.info("No jobs yet")

st.divider()
st.caption("💡 **Stable Edition Features:** Page-by-page processing | Resumable jobs | Graceful failure handling | No connection timeouts")



