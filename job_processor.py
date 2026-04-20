"""
Job-based PDF processor with page-by-page extraction and persistence
Handles timeouts and failures gracefully - never blocks the UI
"""
import os
import json
import time
import re
import fitz  # PyMuPDF
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import pytesseract
from PIL import Image
import numpy as np
import io
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

# Auto-detect Tesseract in local .tesseract folder or use environment variable
def _setup_tesseract():
    """Configure pytesseract to find tesseract executable"""
    # Try local installation first
    local_tesseract = Path(__file__).parent / ".tesseract" / "tesseract.exe"
    if local_tesseract.exists():
        pytesseract.pytesseract.tesseract_cmd = str(local_tesseract)
        return
    
    # Try .env file
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        with open(env_file, 'r') as f:
            for line in f:
                if line.strip().startswith('TESSERACT_CMD='):
                    tess_path = line.split('=', 1)[1].strip()
                    if tess_path and Path(tess_path).exists():
                        pytesseract.pytesseract.tesseract_cmd = tess_path
                        return
    
    # Try environment variable
    env_tesseract = os.getenv('TESSERACT_CMD')
    if env_tesseract and Path(env_tesseract).exists():
        pytesseract.pytesseract.tesseract_cmd = env_tesseract
        return

_setup_tesseract()


def _detect_skew_angle(image: Image.Image, max_angle: float = 5.0) -> float:
    """
    Detect the skew angle of a scanned document using projection profiles.
    
    Rotates a small binarized copy at candidate angles and picks the angle
    where horizontal text lines align best (maximum variance of row sums).
    Typically completes in < 1s per page.
    
    Returns:
        Detected skew angle in degrees (positive = counter-clockwise)
    """
    gray = image.convert('L')
    
    # Aggressive downscale to ~400px on longest side for speed
    w, h = gray.size
    target = 400
    if max(w, h) > target:
        scale = target / max(w, h)
        gray = gray.resize((int(w * scale), int(h * scale)), Image.BILINEAR)
    
    arr = np.array(gray)
    threshold = np.mean(arr)
    binary_img = Image.fromarray(((arr < threshold) * 255).astype(np.uint8))
    
    def score_angle(angle):
        rotated = binary_img.rotate(angle, expand=False, fillcolor=0)
        rot_arr = np.array(rotated)
        bh, bw = rot_arr.shape
        # Crop centre 60% to avoid rotation edge artefacts
        y1, y2 = bh // 5, bh * 4 // 5
        row_sums = np.sum(rot_arr[y1:y2, :], axis=1)
        return np.var(row_sums)
    
    best_angle = 0.0
    best_score = -1.0
    
    # Pass 1: Coarse search (-5° to +5°, step 0.5°)
    for a in np.arange(-max_angle, max_angle + 0.5, 0.5):
        s = score_angle(a)
        if s > best_score:
            best_score = s
            best_angle = a
    
    # Pass 2: Fine search (±0.5°, step 0.1°)
    for a in np.arange(best_angle - 0.5, best_angle + 0.55, 0.1):
        s = score_angle(a)
        if s > best_score:
            best_score = s
            best_angle = a
    
    # Pass 3: Ultra-fine (±0.1°, step 0.02°)
    for a in np.arange(best_angle - 0.1, best_angle + 0.12, 0.02):
        s = score_angle(a)
        if s > best_score:
            best_score = s
            best_angle = a
    
    return round(best_angle, 2)


def deskew_image(image: Image.Image, max_angle: float = 5.0) -> Tuple[Image.Image, float]:
    """
    Detect and correct skew in a scanned document image.
    
    Returns:
        Tuple of (corrected PIL Image, detected angle in degrees).
        Returns the original image unchanged if skew is negligible (< 0.2°).
    """
    angle = _detect_skew_angle(image, max_angle=max_angle)
    
    if abs(angle) < 0.2:
        return image, 0.0
    
    corrected = image.rotate(angle, expand=True,
                             fillcolor=(255, 255, 255),
                             resample=Image.BICUBIC)
    return corrected, angle


class TimeoutException(Exception):
    """Raised when OCR times out"""
    pass


class JobProcessor:
    """Manages PDF processing jobs with persistence and resumability"""
    
    def __init__(self, jobs_dir: str = "jobs"):
        self.jobs_dir = Path(jobs_dir)
        self.jobs_dir.mkdir(exist_ok=True)
    
    def create_job(self, pdf_path: str, username: str, direction: str, country: str) -> str:
        """
        Create a new processing job
        
        Returns:
            job_id: Unique identifier for this job
        """
        # Use microseconds + counter to ensure uniqueness when processing multiple files simultaneously
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        job_id = f"{username}_{timestamp}"
        job_dir = self.jobs_dir / job_id
        
        # Create job directory structure
        (job_dir / "original").mkdir(parents=True, exist_ok=True)
        (job_dir / "pages").mkdir(exist_ok=True)
        
        # Save metadata
        metadata = {
            "job_id": job_id,
            "username": username,
            "direction": direction,
            "country": country,
            "created_at": datetime.now().isoformat(),
            "status": "created",
            "pdf_path": pdf_path,
            "total_pages": 0
        }
        
        with open(job_dir / "metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)
        
        return job_id
    
    def get_job_dir(self, job_id: str) -> Path:
        """Get the directory path for a job"""
        return self.jobs_dir / job_id
    
    def get_job_metadata(self, job_id: str) -> Dict:
        """Load job metadata"""
        metadata_path = self.get_job_dir(job_id) / "metadata.json"
        if metadata_path.exists():
            try:
                with open(metadata_path) as f:
                    content = f.read()
                    if not content.strip():
                        return {"job_id": job_id, "status": "initializing"}
                    return json.loads(content)
            except json.JSONDecodeError:
                return {"job_id": job_id, "status": "initializing"}
        return {}
    
    def update_job_metadata(self, job_id: str, updates: Dict):
        """Update job metadata"""
        metadata = self.get_job_metadata(job_id)
        metadata.update(updates)
        
        with open(self.get_job_dir(job_id) / "metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)
    
    def convert_pdf_to_images(self, job_id: str, pdf_path: str, dpi: int = 200) -> int:
        """
        Convert PDF pages to PNG images using PyMuPDF with auto-rotation
        
        Returns:
            Number of pages converted
        """
        job_dir = self.get_job_dir(job_id)
        pages_dir = job_dir / "pages"
        
        # Open PDF with PyMuPDF
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        
        # Convert each page to image
        for page_num in range(total_pages):
            page = doc[page_num]
            
            # Get page rotation and normalize to 0 degrees
            rotation = page.rotation
            
            # Render page as pixmap at specified DPI with rotation correction
            zoom = dpi / 72  # PDF is 72 DPI by default
            mat = fitz.Matrix(zoom, zoom)
            
            # Apply rotation correction if page is rotated
            if rotation != 0:
                # Rotate to 0 degrees for proper OCR
                mat = mat.prerotate(-rotation)
            
            pix = page.get_pixmap(matrix=mat)
            
            # Save as PNG
            output_path = pages_dir / f"page_{page_num + 1:04d}.png"
            pix.save(str(output_path))
        
        doc.close()
        
        # Update metadata
        self.update_job_metadata(job_id, {
            "total_pages": total_pages,
            "status": "images_created",
            "dpi": dpi
        })
        
        return total_pages
    
    def extract_page_text(self, job_id: str, page_num: int, pdf_path: str, 
                         ocr_timeout: int = 15, retry_dpi: int = 150) -> Dict:
        """
        Extract text from a single page with fallback strategies
        
        Args:
            job_id: Job identifier
            page_num: Page number (1-indexed)
            pdf_path: Path to original PDF
            ocr_timeout: Timeout in seconds for OCR
            retry_dpi: DPI to use for OCR retry
            
        Returns:
            Dict with extraction results
        """
        start_time = time.time()
        job_dir = self.get_job_dir(job_id)
        page_image_path = job_dir / "pages" / f"page_{page_num:04d}.png"
        
        result = {
            "page_number": page_num,
            "status": "pending",
            "method": None,
            "text": "",
            "processing_time": 0,
            "error": None
        }
        
        # Strategy 1: Try embedded PDF text first
        try:
            doc = fitz.open(pdf_path)
            page = doc[page_num - 1]  # 0-indexed
            embedded_text = page.get_text("text").strip()
            doc.close()
            
            # Check if we got meaningful text (>50 chars) AND no corrupt chars
            # Some PDFs have fonts that map price digits to Tamil Unicode block
            # (U+0B00-U+0BFF) – e.g. ௗ (U+0BD7) instead of actual numbers.
            # In that case, fall through to OCR which reads the rendered glyphs.
            has_corrupt_chars = any(0x0B00 <= ord(c) <= 0x0BFF for c in embedded_text)
            
            if len(embedded_text) > 50 and not has_corrupt_chars:
                result["status"] = "success"
                result["method"] = "embedded"
                result["text"] = embedded_text
                result["processing_time"] = time.time() - start_time
                return result
            elif has_corrupt_chars:
                result["error"] = "Embedded text has corrupt Unicode (Tamil block) – falling through to OCR"
        except Exception as e:
            result["error"] = f"Embedded extraction failed: {str(e)}"
        
        # Strategy 2: Try OCR at standard DPI with timeout and auto-rotation
        if page_image_path.exists():
            try:
                def run_ocr():
                    image = Image.open(page_image_path)
                    
                    # Try to detect and correct rotation using Tesseract OSD
                    try:
                        osd = pytesseract.image_to_osd(image)
                        rotation_match = re.search(r'Rotate: (\d+)', osd)
                        if rotation_match:
                            rotation_angle = int(rotation_match.group(1))
                            if rotation_angle != 0:
                                # Rotate image to correct orientation
                                image = image.rotate(-rotation_angle, expand=True)
                    except:
                        # If OSD fails, try all 4 orientations and pick best
                        pass
                    
                    # Deskew: correct slight skew from scanning
                    try:
                        image, skew_angle = deskew_image(image)
                        if abs(skew_angle) >= 0.2:
                            # Save the deskewed image back so the UI shows it corrected
                            image.save(str(page_image_path))
                    except Exception:
                        pass  # deskew is best-effort, never block OCR
                    
                    # Use PSM 6 (uniform block) to better preserve table layout
                    return pytesseract.image_to_string(image, config='--psm 6').strip()
                
                # Use ThreadPoolExecutor for timeout (thread-safe)
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(run_ocr)
                    try:
                        ocr_text = future.result(timeout=ocr_timeout)
                        
                        if len(ocr_text) > 10:
                            result["status"] = "success"
                            result["method"] = "ocr_200dpi"
                            result["text"] = ocr_text
                            result["processing_time"] = time.time() - start_time
                            return result
                    except FutureTimeoutError:
                        result["error"] = f"OCR timeout at 200 DPI after {ocr_timeout}s"
                        future.cancel()
            except Exception as e:
                error_msg = str(e)
                if "tesseract is not installed" in error_msg.lower() or "not in your path" in error_msg.lower():
                    result["error"] = "Tesseract OCR not found. See INSTALL_TESSERACT.md for setup instructions."
                else:
                    result["error"] = f"OCR failed: {error_msg}"
        
        # Strategy 3: Retry OCR at lower DPI with rotation correction
        try:
            def run_ocr_retry():
                # Re-render page at lower DPI with rotation correction
                doc = fitz.open(pdf_path)
                page = doc[page_num - 1]
                
                # Get page rotation from PDF metadata and correct it
                rotation = page.rotation
                zoom = retry_dpi / 72
                mat = fitz.Matrix(zoom, zoom)
                
                # Apply rotation correction if needed
                if rotation != 0:
                    mat = mat.prerotate(-rotation)
                
                pix = page.get_pixmap(matrix=mat)
                
                # Convert to PIL Image
                img_bytes = pix.tobytes("png")
                image = Image.open(io.BytesIO(img_bytes))
                doc.close()
                
                # Try to detect and correct rotation using Tesseract OSD
                try:
                    osd = pytesseract.image_to_osd(image)
                    rotation_match = re.search(r'Rotate: (\d+)', osd)
                    if rotation_match:
                        rotation_angle = int(rotation_match.group(1))
                        if rotation_angle != 0:
                            # Rotate image to correct orientation
                            image = image.rotate(-rotation_angle, expand=True)
                except:
                    # If OSD fails, continue with current orientation
                    pass
                
                # Deskew: correct slight skew from scanning
                try:
                    image, _ = deskew_image(image)
                except Exception:
                    pass  # deskew is best-effort
                
                # Use PSM 6 (uniform block) to better preserve table layout
                return pytesseract.image_to_string(image, config='--psm 6').strip()
            
            # Use ThreadPoolExecutor for timeout (thread-safe)
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(run_ocr_retry)
                try:
                    ocr_text = future.result(timeout=ocr_timeout)
                    
                    if len(ocr_text) > 10:
                        result["status"] = "success"
                        result["method"] = f"ocr_{retry_dpi}dpi_retry"
                        result["text"] = ocr_text
                        result["processing_time"] = time.time() - start_time
                        return result
                except FutureTimeoutError:
                    result["error"] = f"Retry OCR timeout after {ocr_timeout}s"
                    future.cancel()
        except Exception as e:
            error_msg = str(e)
            if "tesseract is not installed" in error_msg.lower() or "not in your path" in error_msg.lower():
                result["error"] = "Tesseract OCR not found. See INSTALL_TESSERACT.md for setup instructions."
            else:
                result["error"] = f"Retry OCR failed: {error_msg}"
        
        # All strategies failed
        result["status"] = "OCR_FAILED"
        result["method"] = "none"
        result["processing_time"] = time.time() - start_time
        
        return result
    
    def process_job(self, job_id: str, pdf_path: str):
        """
        Process entire job page by page with persistence
        Can be resumed if interrupted
        """
        # Load or create pages.json
        job_dir = self.get_job_dir(job_id)
        pages_json_path = job_dir / "pages.json"
        
        if pages_json_path.exists():
            with open(pages_json_path) as f:
                pages_data = json.load(f)
        else:
            pages_data = {"pages": []}
        
        # Get total pages from metadata
        metadata = self.get_job_metadata(job_id)
        total_pages = metadata.get("total_pages", 0)
        
        if total_pages == 0:
            # Convert PDF to images first
            total_pages = self.convert_pdf_to_images(job_id, pdf_path)
        
        # Determine which pages still need processing
        processed_pages = {p["page_number"] for p in pages_data["pages"]}
        
        # Update job status
        self.update_job_metadata(job_id, {"status": "processing"})
        
        # Process each page
        for page_num in range(1, total_pages + 1):
            if page_num in processed_pages:
                continue  # Skip already processed pages (resumability)
            
            # Extract text from this page
            page_result = self.extract_page_text(job_id, page_num, pdf_path)
            
            # Add to pages data
            pages_data["pages"].append(page_result)
            
            # Save after each page (persistence)
            with open(pages_json_path, "w") as f:
                json.dump(pages_data, f, indent=2)
            
            # Update progress in metadata
            progress = len(pages_data["pages"]) / total_pages * 100
            self.update_job_metadata(job_id, {
                "progress": round(progress, 1),
                "pages_processed": len(pages_data["pages"])
            })
        
        # Mark job as complete
        self.update_job_metadata(job_id, {
            "status": "completed",
            "completed_at": datetime.now().isoformat(),
            "progress": 100.0
        })
    
    def get_job_progress(self, job_id: str) -> Dict:
        """
        Get current job progress and page statuses
        
        Returns:
            Dict with progress info and page details
        """
        metadata = self.get_job_metadata(job_id)
        job_dir = self.get_job_dir(job_id)
        pages_json_path = job_dir / "pages.json"
        
        if pages_json_path.exists():
            try:
                with open(pages_json_path) as f:
                    content = f.read()
                    if content.strip():
                        pages_data = json.loads(content)
                    else:
                        pages_data = {"pages": []}
            except json.JSONDecodeError:
                pages_data = {"pages": []}
        else:
            pages_data = {"pages": []}
        
        return {
            "job_id": job_id,
            "status": metadata.get("status", "unknown"),
            "progress": metadata.get("progress", 0),
            "total_pages": metadata.get("total_pages", 0),
            "pages_processed": metadata.get("pages_processed", 0),
            "pages": pages_data.get("pages", [])
        }
    
    def list_jobs(self) -> List[str]:
        """List all job IDs"""
        if not self.jobs_dir.exists():
            return []
        return [d.name for d in self.jobs_dir.iterdir() if d.is_dir()]

    def reprocess_ocr(self, job_id: str):
        """
        Re-run OCR on all pages of an existing job.
        Clears pages.json and re-extracts text (with deskewing).
        Images are preserved; only the OCR text is regenerated.
        """
        job_dir = self.get_job_dir(job_id)
        metadata = self.get_job_metadata(job_id)
        pdf_path = metadata.get('pdf_path', '')
        total_pages = metadata.get('total_pages', 0)

        if not pdf_path or total_pages == 0:
            raise ValueError(f"Job {job_id} has no PDF path or page count")

        pages_json_path = job_dir / "pages.json"

        # Reset
        pages_data = {"pages": []}
        self.update_job_metadata(job_id, {
            "status": "processing",
            "progress": 0,
            "pages_processed": 0
        })

        for page_num in range(1, total_pages + 1):
            page_result = self.extract_page_text(job_id, page_num, pdf_path)
            pages_data["pages"].append(page_result)

            # Save every 5 pages
            if page_num % 5 == 0 or page_num == total_pages:
                with open(pages_json_path, "w") as f:
                    json.dump(pages_data, f, indent=2)

            progress = page_num / total_pages * 100
            self.update_job_metadata(job_id, {
                "progress": round(progress, 1),
                "pages_processed": page_num
            })

        self.update_job_metadata(job_id, {
            "status": "completed",
            "completed_at": datetime.now().isoformat(),
            "progress": 100.0
        })
