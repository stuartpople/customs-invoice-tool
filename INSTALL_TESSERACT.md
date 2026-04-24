# Installing Tesseract OCR (No Admin Required)

## Quick Setup (5 minutes)

### Step 1: Download Tesseract
1. Download the portable version from: https://github.com/UB-Mannheim/tesseract/wiki
   - Look for "tesseract-ocr-w64-setup-5.3.x.exe" (latest version)
   - OR use direct link: https://digi.bib.uni-mannheim.de/tesseract/tesseract-ocr-w64-setup-5.3.3.20231005.exe

2. Run the installer
   - **Important**: When asked where to install, change the path to:
     ```
     C:\Users\stuart.pople\repos\customs-invoice-tool\.tesseract
     ```
   - This installs it locally in your project folder (no admin needed)
   - Click through the installer accepting defaults

### Step 2: Verify Installation
After installation, you should have:
```
C:\Users\stuart.pople\repos\customs-invoice-tool\.tesseract\tesseract.exe
```

### Step 3: Configure the App
The app will automatically detect Tesseract in the `.tesseract` folder!

No manual configuration needed - just restart Streamlit after installing.

### Step 4: Restart Streamlit
1. Stop the current Streamlit server (Ctrl+C in terminal)
2. Run: `.\.miniconda\python.exe -m streamlit run app.py`
3. Upload your PDF again

---

## Alternative: Manual Configuration

If automatic detection doesn't work, add this to your environment:

1. Create/edit `.env` file in the repo root:
   ```
   TESSERACT_CMD=C:\Users\stuart.pople\repos\customs-invoice-tool\.tesseract\tesseract.exe
   ```

2. Restart Streamlit

---

## Troubleshooting

### "tesseract is not installed or it's not in your PATH"
- Check that `tesseract.exe` exists in `.tesseract` folder
- Try the manual `.env` configuration above
- Restart Streamlit completely

### Installation failed
- Try running installer as user (not admin)
- Make sure you changed the install path to your repo folder

### Still not working?
Run this test in a terminal:
```powershell
.\.tesseract\tesseract.exe --version
```

If that works, the app should detect it!

---

## What Does Tesseract Do?

Tesseract reads text from PDF images (scanned documents). Your PDF appears to be image-based, so every page needs OCR to extract the text before parsing can find invoice line items.
