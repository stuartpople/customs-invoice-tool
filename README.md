# 🚢 Customs Invoice Tool

A comprehensive wizard-based tool for processing customs invoices with HMRC Trade Tariff API integration, OCR support, and smart import/export handling.

## ✨ Key Features

### 🤖 Smart Automation

1. **Auto-Detected Username**
   - Automatically captures system username for audit trail
   - No manual entry required - just verify and proceed

2. **OCR for Image-Based PDFs**
   - Handles both text-based and scanned PDFs
   - Automatic fallback to Tesseract OCR for image PDFs
   - High-quality text extraction at 300 DPI

3. **Intelligent Import/Export Mode**
   - **Import Mode**: Requires 10-digit commodity codes, shows import duties, licences, quotas
   - **Export Mode**: Accepts 8-digit codes, displays export restrictions and requirements
   - Automatic validation and smart padding

### 📄 PDF Processing

- **Drag & Drop Interface** - Upload multiple invoices simultaneously
- **Automatic Extraction** - Pattern matching for commodity codes, values, quantities
- **Debug View** - See extracted text to troubleshoot issues
- **Multi-format Support** - Text PDFs and scanned/image PDFs

### 🔍 HMRC Integration

- Real-time lookups against UK Trade Tariff API
- Direction-aware queries (import vs export)
- Retrieves:
  - Full commodity descriptions
  - Supplementary unit requirements
  - Import duties and measures
  - Export restrictions and licences
  - Document codes
  - Quota information

### 📊 Smart Consolidation

- Groups line items by commodity code
- Calculates totals (quantity, value, weight)
- Aggregates countries of origin
- Creates declaration-ready summaries

### ✏️ Interactive Editing

- Spreadsheet-like data editor
- Add/edit/remove line items
- Real-time validation based on import/export mode
- Mandatory field enforcement

### 💾 Export Options

- Excel (.xlsx) with professional formatting
- CSV for data exchange
- Formatted for customs declarations

## 🎯 Wizard Workflow

### Step 1: Setup
- **Auto-detected username** (from system, editable)
- Select Import/Export direction
- **Country dropdown** with searchable list of all countries
- **Common countries** at top for quick selection
- Optional job reference
- **Smart hints** based on direction chosen

### Step 2: Upload PDFs
- Drag & drop PDF invoices
- Multiple file support
- File size preview
- **Automatic text extraction** (OCR if needed)

### Step 3: Extract & Edit Data
- **Extraction metrics** showing completeness
- **Debug view** shows extracted text
- Review automatically extracted line items
- Edit data in spreadsheet view
- **Clear all** option to start fresh
- **Validated manual entry** (10 digits for import, 8+ for export)
- **Country dropdown** with all world countries + common countries for quick access
- Clear error messages for validation failures

### Step 4: Enrich with HMRC
- Automatic HMRC API queries (direction-aware)
- Real-time progress tracking
- View tariff information
- **Import-specific info**: licences, quotas, duties
- **Export-specific info**: restrictions, export licences

### Step 5: Review & Export
- Consolidated summary by commodity code
- Job summary with full audit trail
- Download Excel or CSV
- Start new job

## 📦 Installation

```bash
# Install system dependencies (for OCR)
sudo apt-get update
sudo apt-get install -y tesseract-ocr poppler-utils

# Install Python dependencies
pip install -r requirements.txt

# Run the application
streamlit run app.py
```

## � Deployment for Office Teams

**Want your entire office to access this tool?**

See the complete **[Office Deployment Guide](OFFICE_DEPLOYMENT_GUIDE.md)** for step-by-step instructions to:
- ✅ Run on a local office computer (FREE, data stays private)
- ✅ Let your team access via `http://office-computer:8500`
- ✅ Set up auto-start on boot
- ✅ No cloud uploads - 100% private within your network

**Quick Start:**

**Windows:**
```bash
# Double-click to start:
START_SERVER.bat
```

**Mac/Linux:**
```bash
# Run the startup script:
./START_SERVER.sh
```

Then share `http://YOUR-IP:8500` with your team!

## �🔧 Requirements

### System Dependencies
- **Tesseract OCR** - For image-based PDF processing
- **Poppler** - For PDF to image conversion

### Python Packages
- streamlit >= 1.30.0
- PyPDF2 >= 3.0.0 (text extraction)
- pytesseract >= 0.3.10 (OCR)
- pdf2image >= 1.16.0 (PDF conversion)
- Pillow >= 10.0.0 (image processing)
- requests >= 2.31.0 (HMRC API)
- openpyxl >= 3.1.0 (Excel export)
- pandas >= 2.0.0 (data handling)

## 🏗️ Architecture

```
customs-invoice-tool/
├── app.py                  # Main wizard UI with smart features
├── pdf_extractor.py        # PDF + OCR text extraction
├── hmrc_api.py            # Direction-aware HMRC API integration
├── consolidation.py       # Data grouping and export
├── requirements.txt       # All dependencies
└── README.md             # This file
```

## 🔌 HMRC API Integration

- **API**: https://www.trade-tariff.service.gov.uk/api/v2
- **No API key required** for basic lookups
- **Direction-aware queries** return relevant measures
- **Automatic code padding** for correct lookups
- **Rate limiting** handled gracefully

## 💡 Usage Tips

### For Image-Based PDFs
- System automatically detects and uses OCR
- Works with scanned invoices
- Check debug view to see extracted text quality
- Higher quality scans = better OCR results

### Import vs Export
- **Import**: System enforces 10-digit codes, shows duties/quotas
- **Export**: Accepts 8-digit codes, shows export restrictions
- Direction setting affects all validation and API queries

### Data Quality
- Always review extracted data in Step 3
- Use debug view to troubleshoot extraction issues
- Manually add/edit items as needed
- Validation prevents incorrect codes

### HMRC Data
- Some codes may not return full data
- Always verify critical information
- Licence and quota info is indicative
- Check official sources for final declarations

## 🆕 Recent Enhancements

### v2.0 Features
✅ **OCR Support** - Process scanned/image PDFs automatically
✅ **Auto Username** - System username captured automatically  
✅ **Smart Import/Export** - Direction-aware validation and HMRC queries
✅ **10-digit Enforcement** - Import mode requires full commodity codes
✅ **Debug View** - Troubleshoot PDF extraction issues
✅ **Enhanced Validation** - Real-time code validation with helpful errors
✅ **Country Dropdown** - Searchable list of all world countries
✅ **Extraction Metrics** - See completeness of extracted data at a glance
✅ **Improved Parser** - Better detection of quantities, values, and weights
- Multi-user collaboration
- PDF invoice generation from data

## 📊 Output Format

Excel export includes:
- Commodity Code (validated 8-10 digits)
- Description (PDF + HMRC)
- Item Count (grouped items)
- Total Quantity
- Total Value (£)
- Net Weight (kg)
- Countries of Origin
- Supplementary Units
- Import Duties (import mode)
- Export Restrictions (export mode)

## 🛠️ Development

**Built with:**
- Streamlit - Modern web framework
- PyPDF2 + Pytesseract - PDF/OCR processing
- Pandas - Data manipulation
- OpenPyXL - Excel generation
- Requests - HTTP API integration

## ⚠️ Important Notes

### Audit Trail
- Username automatically captured from system
- All actions timestamped
- Job ID generated for tracking

### Accuracy
- OCR quality depends on scan quality
- Always verify extracted data
- Commodity classifications are user responsibility
- HMRC data is indicative - verify with official sources

### Legal
- Tool assists but does not replace professional customs advice
- User responsible for declaration accuracy
- Use at your own risk for official submissions

## 🤝 Support

For issues, enhancements, or questions, refer to the project repository.

## 📝 License

Provided as-is for customs processing workflows.