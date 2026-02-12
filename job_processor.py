"""
Job-based PDF processor with page-by-page extraction and persistence
Handles timeouts and failures gracefully - never blocks the UI
"""
import os
import json
import time
import fitz  # PyMuPDF
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
import pytesseract
from PIL import Image
import io
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError


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
        Convert PDF pages to PNG images using PyMuPDF
        
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
            
            # Render page as pixmap at specified DPI
            zoom = dpi / 72  # PDF is 72 DPI by default
            mat = fitz.Matrix(zoom, zoom)
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
                         ocr_timeout: int = 8, retry_dpi: int = 150) -> Dict:
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
            
            # Check if we got meaningful text (>50 chars)
            if len(embedded_text) > 50:
                result["status"] = "success"
                result["method"] = "embedded"
                result["text"] = embedded_text
                result["processing_time"] = time.time() - start_time
                return result
        except Exception as e:
            result["error"] = f"Embedded extraction failed: {str(e)}"
        
        # Strategy 2: Try OCR at standard DPI with timeout
        if page_image_path.exists():
            try:
                def run_ocr():
                    image = Image.open(page_image_path)
                    return pytesseract.image_to_string(image).strip()
                
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
                result["error"] = f"OCR failed: {str(e)}"
        
        # Strategy 3: Retry OCR at lower DPI
        try:
            def run_ocr_retry():
                # Re-render page at lower DPI
                doc = fitz.open(pdf_path)
                page = doc[page_num - 1]
                zoom = retry_dpi / 72
                mat = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=mat)
                
                # Convert to PIL Image
                img_bytes = pix.tobytes("png")
                image = Image.open(io.BytesIO(img_bytes))
                doc.close()
                
                return pytesseract.image_to_string(image).strip()
            
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
            result["error"] = f"Retry OCR failed: {str(e)}"
        
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
