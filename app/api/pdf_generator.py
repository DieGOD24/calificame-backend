import asyncio
import base64
import io
import os
import tempfile
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from fpdf import FPDF
from loguru import logger
from PIL import Image
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_db
from app.config import settings
from app.models.task_log import TaskLog
from app.models.user import User
from app.rate_limit import limiter
from app.services.image_processing import process_image, process_image_ai

router = APIRouter(prefix="/pdf-generator", tags=["PDF Generator"])

# Image processing constants
DEFAULT_DPI = 96
A4_WIDTH_MM = 210
A4_HEIGHT_MM = 297


def generate_pdf_from_images(image_bytes_list: list[bytes]) -> bytes:
    """Generate a PDF from a list of image byte arrays, one image per page."""
    pdf = FPDF()
    for img_bytes in image_bytes_list:
        img = Image.open(io.BytesIO(img_bytes))
        w_mm = img.width * 25.4 / DEFAULT_DPI
        h_mm = img.height * 25.4 / DEFAULT_DPI
        # Fit to A4
        scale = min(A4_WIDTH_MM / w_mm, A4_HEIGHT_MM / h_mm, 1.0)
        final_w = w_mm * scale
        final_h = h_mm * scale
        pdf.add_page(orientation="P" if final_h >= final_w else "L")
        # Save temp file for fpdf
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp, format="PNG")
            tmp_path = tmp.name
        try:
            page_w = A4_WIDTH_MM if final_h >= final_w else A4_HEIGHT_MM
            page_h = A4_HEIGHT_MM if final_h >= final_w else A4_WIDTH_MM
            x = (page_w - final_w) / 2
            y = (page_h - final_h) / 2
            pdf.image(tmp_path, x=x, y=y, w=final_w, h=final_h)
        finally:
            os.unlink(tmp_path)
    return pdf.output()


@router.post("/analyze")
@limiter.limit(settings.RATE_LIMIT_UPLOAD)
async def analyze_images(
    request: Request,
    files: list[UploadFile],
    current_user: User = Depends(get_current_active_user),
) -> list[dict]:
    """Receive uploaded images and return processed (cropped + enhanced) previews.

    Each entry in the response carries either a processed preview or an
    `error` string explaining why that one image failed. The endpoint only
    raises 400 when *every* image failed — partial failures are surfaced
    per-image so the frontend can keep the good ones and let the user retry
    or remove the broken ones. Previously a single bad image (corrupt EXIF,
    unsupported format, transient OOM in cv2.imdecode, ...) made the entire
    batch fail with 400, which looked to the user like the whole wizard
    was broken.
    """
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No images provided")

    # Read every upload up-front so we don't hold UploadFile handles across the
    # parallel processing stage. Reads themselves are sequential (each await
    # consumes a single connection) but cheap.
    raw_inputs: list[tuple[int, str | None, bytes | None, str | None]] = []
    for index, upload in enumerate(files):
        try:
            data = await upload.read()
            raw_inputs.append((index, upload.filename, data, None))
        except Exception as exc:
            logger.error("Error reading upload {} ({}): {}", index, upload.filename, exc)
            raw_inputs.append((index, upload.filename, None, str(exc)))

    use_ai = bool(settings.USE_AI_PREPROCESSING)
    cpu_fn = process_image_ai if use_ai else process_image
    semaphore = asyncio.Semaphore(max(1, int(settings.AI_PREPROCESSING_CONCURRENCY)))
    loop = asyncio.get_running_loop()

    async def _process_one(idx: int, filename: str | None, data: bytes | None, read_err: str | None) -> dict:
        if read_err is not None or data is None:
            return {
                "index": idx,
                "original_width": None,
                "original_height": None,
                "processed_image_base64": None,
                "error": read_err or "Could not read upload",
            }
        try:
            original = Image.open(io.BytesIO(data))
            orig_w, orig_h = original.width, original.height
        except Exception as exc:
            logger.error("Image decode failed for {} ({}): {}", idx, filename, exc)
            return {
                "index": idx,
                "original_width": None,
                "original_height": None,
                "processed_image_base64": None,
                "error": str(exc),
            }

        try:
            async with semaphore:
                processed = await loop.run_in_executor(None, cpu_fn, data)
            return {
                "index": idx,
                "original_width": orig_w,
                "original_height": orig_h,
                "processed_image_base64": base64.b64encode(processed).decode("utf-8"),
                "error": None,
            }
        except Exception as exc:
            logger.error("Error analyzing image {} ({}): {}", idx, filename, exc)
            return {
                "index": idx,
                "original_width": orig_w,
                "original_height": orig_h,
                "processed_image_base64": None,
                "error": str(exc),
            }

    results = await asyncio.gather(*(_process_one(*item) for item in raw_inputs))
    results = sorted(results, key=lambda r: r["index"])
    success_count = sum(1 for r in results if r["error"] is None)

    if success_count == 0:
        first_err = next((r["error"] for r in results if r.get("error")), "Unknown error")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No se pudo procesar ninguna imagen: {first_err}",
        )

    logger.info(
        "User {} analyzed {} images (ai={}): {} ok, {} failed",
        current_user.id,
        len(results),
        use_ai,
        success_count,
        len(results) - success_count,
    )
    return results


@router.post("/crop")
async def crop_image(
    file: UploadFile,
    x: int = Query(..., ge=0),
    y: int = Query(..., ge=0),
    width: int = Query(..., gt=0),
    height: int = Query(..., gt=0),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """Crop an image with the given coordinates and return as base64."""
    try:
        image_bytes = await file.read()
        img = Image.open(io.BytesIO(image_bytes))

        # Validate crop coordinates
        if x + width > img.width or y + height > img.height:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Crop coordinates exceed image dimensions",
            )

        cropped = img.crop((x, y, x + width, y + height))
        buffer = io.BytesIO()
        cropped.save(buffer, format="PNG")
        cropped_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        logger.info(f"User {current_user.id} cropped image to ({x},{y},{width},{height})")
        return {"cropped_image_base64": cropped_b64, "width": width, "height": height}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cropping image: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to crop image: {str(e)}",
        )


@router.post("/generate")
@limiter.limit(settings.RATE_LIMIT_UPLOAD)
async def generate_pdf(
    request: Request,
    files: list[UploadFile],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> StreamingResponse:
    """Generate a PDF from ordered images. Each image becomes a page.

    Images are inserted as received: the frontend already sends the
    processed PNGs returned by ``/analyze``, so re-running the pipeline
    here would apply smart-crop + text enhancement twice and the PDF
    would no longer match the preview the user accepted.
    """
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No images provided")

    try:
        image_bytes_list = []
        for upload in files:
            img_bytes = await upload.read()
            image_bytes_list.append(img_bytes)

        pdf_bytes = generate_pdf_from_images(image_bytes_list)

        # Create a TaskLog entry
        task = TaskLog(
            user_id=current_user.id,
            task_type="pdf_generation",
            status="completed",
            progress=100.0,
            current_step="PDF generated",
            result_data={
                "page_count": len(image_bytes_list),
                "timestamp": datetime.now(UTC).isoformat(),
            },
            completed_at=datetime.now(UTC),
        )
        db.add(task)
        db.commit()

        logger.info(f"User {current_user.id} generated PDF with {len(image_bytes_list)} pages (task={task.id})")

        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=generated.pdf"},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating PDF: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate PDF: {str(e)}",
        )
