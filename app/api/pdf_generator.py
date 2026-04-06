import base64
import io
import os
import tempfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from fpdf import FPDF
from loguru import logger
from PIL import Image
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_db
from app.models.task_log import TaskLog
from app.models.user import User

router = APIRouter(prefix="/pdf-generator", tags=["PDF Generator"])


def auto_detect_crop(image_bytes: bytes) -> dict:
    """Detect document edges in an image and return crop coordinates."""
    img = Image.open(io.BytesIO(image_bytes))
    gray = img.convert("L")
    threshold = 240
    pixels = gray.load()
    w, h = gray.size
    min_x, min_y, max_x, max_y = w, h, 0, 0
    for y_pos in range(h):
        for x_pos in range(w):
            if pixels[x_pos, y_pos] < threshold:
                min_x = min(min_x, x_pos)
                min_y = min(min_y, y_pos)
                max_x = max(max_x, x_pos)
                max_y = max(max_y, y_pos)
    padding = 10
    min_x = max(0, min_x - padding)
    min_y = max(0, min_y - padding)
    max_x = min(w, max_x + padding)
    max_y = min(h, max_y + padding)
    if max_x <= min_x or max_y <= min_y:
        return {"x": 0, "y": 0, "width": w, "height": h}
    return {"x": min_x, "y": min_y, "width": max_x - min_x, "height": max_y - min_y}


def generate_pdf_from_images(image_bytes_list: list[bytes]) -> bytes:
    """Generate a PDF from a list of image byte arrays, one image per page."""
    pdf = FPDF()
    for img_bytes in image_bytes_list:
        img = Image.open(io.BytesIO(img_bytes))
        w_mm = img.width * 25.4 / 96  # assume 96 DPI
        h_mm = img.height * 25.4 / 96
        # Fit to A4
        a4_w, a4_h = 210, 297
        scale = min(a4_w / w_mm, a4_h / h_mm, 1.0)
        final_w = w_mm * scale
        final_h = h_mm * scale
        pdf.add_page(orientation="P" if final_h >= final_w else "L")
        # Save temp file for fpdf
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp, format="PNG")
            tmp_path = tmp.name
        try:
            page_w = a4_w if final_h >= final_w else a4_h
            page_h = a4_h if final_h >= final_w else a4_w
            x = (page_w - final_w) / 2
            y = (page_h - final_h) / 2
            pdf.image(tmp_path, x=x, y=y, w=final_w, h=final_h)
        finally:
            os.unlink(tmp_path)
    return pdf.output()


@router.post("/analyze")
async def analyze_images(
    images: list[UploadFile],
    current_user: User = Depends(get_current_active_user),
) -> list[dict]:
    """Receive uploaded images and return auto-detected crop coordinates for each."""
    if not images:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No images provided")

    results = []
    for index, upload in enumerate(images):
        try:
            image_bytes = await upload.read()
            img = Image.open(io.BytesIO(image_bytes))
            crop_box = auto_detect_crop(image_bytes)

            # Crop the image and encode as base64
            cropped = img.crop((
                crop_box["x"],
                crop_box["y"],
                crop_box["x"] + crop_box["width"],
                crop_box["y"] + crop_box["height"],
            ))
            buffer = io.BytesIO()
            cropped.save(buffer, format="PNG")
            cropped_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

            results.append({
                "index": index,
                "original_width": img.width,
                "original_height": img.height,
                "crop_box": crop_box,
                "cropped_image_base64": cropped_b64,
            })
        except Exception as e:
            logger.error(f"Error analyzing image {index}: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to process image at index {index}: {str(e)}",
            )

    logger.info(f"User {current_user.id} analyzed {len(results)} images for cropping")
    return results


@router.post("/crop")
async def crop_image(
    image: UploadFile,
    x: int = Query(..., ge=0),
    y: int = Query(..., ge=0),
    width: int = Query(..., gt=0),
    height: int = Query(..., gt=0),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """Crop an image with the given coordinates and return as base64."""
    try:
        image_bytes = await image.read()
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
async def generate_pdf(
    images: list[UploadFile],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> StreamingResponse:
    """Generate a PDF from ordered images. Each image becomes a page."""
    if not images:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No images provided")

    try:
        image_bytes_list = []
        for upload in images:
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
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            completed_at=datetime.now(timezone.utc),
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
