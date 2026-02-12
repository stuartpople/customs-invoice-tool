# IT DEPLOYMENT GUIDE
## Customs Invoice Tool - Internal Network Deployment

**Document Version:** 1.0  
**Date:** February 12, 2026  
**Contact:** Stuart Pople

---

## EXECUTIVE SUMMARY

This document provides instructions for deploying the Customs Invoice Tool on the internal office network. The application is a Python-based web application that processes customs invoices, integrates with HMRC APIs, and extracts data from PDF/Word/Excel documents.

**Key Benefits:**
- ✅ Process customs invoices automatically
- ✅ HMRC Trade Tariff API integration
- ✅ OCR support for scanned documents
- ✅ Export to Excel/CSV
- ✅ SQLite database for audit trail
- ✅ Auto-capture user identity for compliance
- ✅ 100% free, open-source software

---

## DEPLOYMENT OPTIONS

### Option 1: Dedicated Windows Server/Workstation (RECOMMENDED)
Deploy on a single Windows machine that remains powered on. Users access via browser.

### Option 2: Docker Container
Deploy in a containerized environment for easier management.

### Option 3: Linux Server
Deploy on existing Linux infrastructure.

---

## OPTION 1: WINDOWS SERVER DEPLOYMENT

### System Requirements

**Minimum:**
- Windows 10/11 or Windows Server 2016+
- 4GB RAM
- 2GB free disk space
- Network connectivity
- Ports: 8501 (configurable)

**Software Dependencies:**
- Python 3.9 or higher
- Tesseract OCR (for scanned PDF support)
- Poppler (for PDF processing)

### Installation Steps

#### Step 1: Install Python

Download and install Python 3.11 from:
```
https://www.python.org/downloads/
```

**Important:** During installation, check "Add Python to PATH"

Verify installation:
```cmd
python --version
pip --version
```

#### Step 2: Install System Dependencies

**Tesseract OCR:**
Download from: https://github.com/UB-Mannheim/tesseract/wiki
Install to: `C:\Program Files\Tesseract-OCR`

Add to system PATH:
```
C:\Program Files\Tesseract-OCR
```

**Poppler:**
Download from: https://github.com/oschwartz10612/poppler-windows/releases
Extract to: `C:\Program Files\poppler`

Add to system PATH:
```
C:\Program Files\poppler\Library\bin
```

#### Step 3: Clone/Download Application

**Option A - Using Git:**
```cmd
cd C:\inetpub\wwwroot
git clone https://github.com/stuartpople/customs-invoice-tool.git
cd customs-invoice-tool
```

**Option B - Manual Download:**
1. Download ZIP from: https://github.com/stuartpople/customs-invoice-tool/archive/refs/heads/main.zip
2. Extract to: `C:\inetpub\wwwroot\customs-invoice-tool`

#### Step 4: Install Python Dependencies

```cmd
cd C:\inetpub\wwwroot\customs-invoice-tool
pip install -r requirements.txt
```

Expected packages:
- streamlit (web framework)
- PyPDF2 (PDF processing)
- pytesseract (OCR)
- openpyxl (Excel export)
- pandas (data processing)
- requests (API calls)

#### Step 5: Configure Application

Edit `.streamlit/config.toml` (if needed):
```toml
[server]
port = 8501
address = "0.0.0.0"
enableCORS = false
enableXsrfProtection = true

[browser]
serverAddress = "your-server-name"
```

#### Step 6: Configure Firewall

Open Windows Firewall and allow inbound connections on port 8501:

```powershell
New-NetFirewallRule -DisplayName "Customs Invoice Tool" -Direction Inbound -LocalPort 8501 -Protocol TCP -Action Allow
```

#### Step 7: Run Application

**Manual Start (for testing):**
```cmd
cd C:\inetpub\wwwroot\customs-invoice-tool
streamlit run app.py
```

**Access at:** `http://localhost:8501`

#### Step 8: Set Up Auto-Start (Production)

Create a Windows Service using NSSM (Non-Sucking Service Manager):

1. Download NSSM: https://nssm.cc/download
2. Install service:

```cmd
nssm install CustomsInvoiceTool "C:\Python311\python.exe"
nssm set CustomsInvoiceTool AppDirectory "C:\inetpub\wwwroot\customs-invoice-tool"
nssm set CustomsInvoiceTool AppParameters "-m streamlit run app.py --server.port=8501 --server.address=0.0.0.0"
nssm set CustomsInvoiceTool DisplayName "Customs Invoice Tool"
nssm set CustomsInvoiceTool Description "Internal customs invoice processing tool"
nssm set CustomsInvoiceTool Start SERVICE_AUTO_START
nssm start CustomsInvoiceTool
```

---

## OPTION 2: DOCKER DEPLOYMENT

### Dockerfile

Create `Dockerfile` in the application directory:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Copy application files
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8501

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

### Docker Compose

Create `docker-compose.yml`:

```yaml
version: '3.8'

services:
  customs-tool:
    build: .
    ports:
      - "8501:8501"
    volumes:
      - ./data:/app/data
      - ./conversion_history.db:/app/conversion_history.db
    restart: unless-stopped
    environment:
      - TZ=Europe/London
```

### Deploy

```bash
docker-compose up -d
```

---

## OPTION 3: LINUX SERVER DEPLOYMENT

### Ubuntu/Debian

```bash
# Update system
sudo apt-get update
sudo apt-get upgrade -y

# Install Python and dependencies
sudo apt-get install -y python3.11 python3-pip tesseract-ocr poppler-utils git

# Clone application
cd /opt
sudo git clone https://github.com/stuartpople/customs-invoice-tool.git
cd customs-invoice-tool

# Install Python packages
sudo pip3 install -r requirements.txt

# Create systemd service
sudo nano /etc/systemd/system/customs-tool.service
```

**Service file content:**
```ini
[Unit]
Description=Customs Invoice Tool
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/customs-invoice-tool
ExecStart=/usr/bin/python3 -m streamlit run app.py --server.port=8501 --server.address=0.0.0.0
Restart=always

[Install]
WantedBy=multi-user.target
```

**Enable and start:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable customs-tool
sudo systemctl start customs-tool
sudo systemctl status customs-tool
```

---

## NETWORK ACCESS CONFIGURATION

### Internal DNS (Recommended)

Create internal DNS entry:
```
customs-tool.company.local → [Server IP]
```

Users access via: `http://customs-tool.company.local:8501`

### Reverse Proxy with IIS/Nginx (Optional)

For HTTPS and friendly URLs:

**Nginx configuration:**
```nginx
server {
    listen 80;
    server_name customs-tool.company.local;
    
    location / {
        proxy_pass http://localhost:8501;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

---

## SECURITY CONSIDERATIONS

### Data Storage
- SQLite database stored locally: `conversion_history.db`
- Contains: timestamps, usernames, job references, commodity codes
- **No sensitive financial data stored**

### Network Security
- Application binds to `0.0.0.0` (all interfaces)
- Restrict access via firewall rules to internal network only
- Consider VPN requirement for remote access

### User Authentication
- Currently uses Windows username (auto-captured)
- No password authentication (trusted internal network)
- Consider adding Active Directory integration if needed

### API Access
- HMRC Trade Tariff API: Public, no authentication required
- No outbound restrictions needed
- Monitor API usage (rate limits may apply)

---

## MONITORING & MAINTENANCE

### Log Files
- Application logs: Check terminal/service output
- Conversion history: `conversion_history.db`
- Streamlit logs: `~/.streamlit/logs/`

### Database Backups
Backup SQLite database regularly:
```cmd
copy conversion_history.db conversion_history_%date%.db
```

### Updates
```bash
cd /path/to/customs-invoice-tool
git pull
pip install -r requirements.txt --upgrade
# Restart service
```

---

## TROUBLESHOOTING

### Application won't start
- Check Python installation: `python --version`
- Check dependencies: `pip list`
- Check firewall rules
- Check port availability: `netstat -an | findstr 8501`

### OCR not working
- Verify Tesseract installation: `tesseract --version`
- Check PATH environment variable
- Test: `tesseract --list-langs`

### HMRC API errors
- Check internet connectivity
- Verify API endpoint: https://www.trade-tariff.service.gov.uk/api/v2
- Check for rate limiting (429 errors)

### Database issues
- Check file permissions on `conversion_history.db`
- Ensure directory is writable
- Check disk space

---

## SUPPORT CONTACTS

**Application Owner:** Stuart Pople  
**Repository:** https://github.com/stuartpople/customs-invoice-tool  
**Documentation:** See README.md in repository

---

## APPENDIX A: PORT REQUIREMENTS

| Port | Protocol | Purpose | Inbound/Outbound |
|------|----------|---------|------------------|
| 8501 | TCP | Streamlit web interface | Inbound (Internal) |
| 443 | TCP | HMRC API (HTTPS) | Outbound |
| 80 | TCP | HTTP access (optional) | Inbound (Internal) |

---

## APPENDIX B: QUICK START SCRIPT

Save as `deploy.bat`:

```batch
@echo off
echo Installing Python packages...
pip install -r requirements.txt

echo Starting Customs Invoice Tool...
echo.
echo Access the application at:
echo http://%COMPUTERNAME%:8501
echo.
streamlit run app.py --server.port=8501 --server.address=0.0.0.0
```

---

**END OF DOCUMENT**

*Print this document for your IT department*
