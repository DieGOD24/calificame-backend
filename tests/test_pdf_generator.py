import io
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from PIL import Image

from app.models.user import User


def _make_png_bytes(width: int = 100, height: int = 100, color: str = "white") -> bytes:
    """Create a simple in-memory PNG image."""
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()


def _make_png_with_content(width: int = 200, height: int = 200) -> bytes:
    """Create a PNG with a dark rectangle so auto-detect finds non-white pixels."""
    img = Image.new("RGB", (width, height), "white")
    # Draw a dark area in the center
    for y in range(50, 150):
        for x in range(50, 150):
            img.putpixel((x, y), (0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()


class TestAnalyzeImages:
    def test_upload_valid_png(self, client: TestClient, test_user: User, auth_headers: dict) -> None:
        png_data = _make_png_with_content()
        response = client.post(
            "/api/v1/pdf-generator/analyze",
            headers=auth_headers,
            files=[("files", ("test.png", png_data, "image/png"))],
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert "processed_image_base64" in data[0]
        assert data[0]["original_width"] == 200
        assert data[0]["original_height"] == 200
        # Verify processed_image_base64 is valid base64
        import base64

        decoded = base64.b64decode(data[0]["processed_image_base64"])
        assert len(decoded) > 0

    def test_no_files(self, client: TestClient, auth_headers: dict) -> None:
        # Sending an empty list of files results in a validation error (422)
        # because FastAPI requires at least the field to be present.
        response = client.post(
            "/api/v1/pdf-generator/analyze",
            headers=auth_headers,
            files=[],
        )
        assert response.status_code in (400, 422)

    def test_one_valid_one_invalid_returns_200_with_per_image_errors(
        self,
        client: TestClient,
        test_user: User,
        auth_headers: dict,
    ) -> None:
        """Mixed batch: the valid one is processed, the invalid one carries an
        `error` field. Whole request still returns 200 so the user can keep
        the working photos."""
        valid_png = _make_png_with_content()
        garbage = b"this-is-not-an-image"
        response = client.post(
            "/api/v1/pdf-generator/analyze",
            headers=auth_headers,
            files=[
                ("files", ("ok.png", valid_png, "image/png")),
                ("files", ("bad.bin", garbage, "image/png")),
            ],
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert len(data) == 2

        ok = data[0]
        bad = data[1]
        assert ok["error"] is None
        assert ok["processed_image_base64"]
        assert bad["error"]
        assert bad["processed_image_base64"] is None

    def test_all_invalid_returns_400(
        self,
        client: TestClient,
        test_user: User,
        auth_headers: dict,
    ) -> None:
        """If *every* image in the batch failed, surface 400 so the user
        knows nothing landed on the server."""
        garbage = b"definitely-not-an-image"
        response = client.post(
            "/api/v1/pdf-generator/analyze",
            headers=auth_headers,
            files=[
                ("files", ("a.bin", garbage, "image/png")),
                ("files", ("b.bin", garbage, "image/png")),
            ],
        )
        assert response.status_code == 400
        # The detail should include something useful, not just a generic msg.
        assert "imagen" in response.json()["detail"].lower()


class TestCropImage:
    def test_valid_crop(self, client: TestClient, test_user: User, auth_headers: dict) -> None:
        png_data = _make_png_bytes(200, 200)
        response = client.post(
            "/api/v1/pdf-generator/crop",
            headers=auth_headers,
            params={"x": 10, "y": 10, "width": 50, "height": 50},
            files={"file": ("img.png", png_data, "image/png")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["width"] == 50
        assert data["height"] == 50
        assert "cropped_image_base64" in data

    def test_crop_exceeds_dimensions(self, client: TestClient, test_user: User, auth_headers: dict) -> None:
        png_data = _make_png_bytes(100, 100)
        response = client.post(
            "/api/v1/pdf-generator/crop",
            headers=auth_headers,
            params={"x": 50, "y": 50, "width": 80, "height": 80},
            files={"file": ("img.png", png_data, "image/png")},
        )
        assert response.status_code == 400


class TestImageProcessing:
    def test_smart_crop_detects_white_rectangle_on_dark_bg(self) -> None:
        """A white rectangle (paper) on a dark background should be cropped to the rectangle."""
        from app.services.image_processing import smart_crop

        # Create dark background with white rectangle in center
        img = Image.new("RGB", (400, 500), (50, 50, 50))  # dark bg
        for y in range(100, 400):
            for x in range(80, 320):
                img.putpixel((x, y), (255, 255, 255))  # white paper
        # Add some "text" (dark pixels) on the paper
        for y in range(150, 160):
            for x in range(120, 280):
                img.putpixel((x, y), (0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        original_bytes = buf.getvalue()

        cropped_bytes = smart_crop(original_bytes)
        result = Image.open(io.BytesIO(cropped_bytes))
        # Cropped result should be smaller than original (removed dark borders)
        assert result.width < 400 or result.height < 500

    def test_enhance_text_darkens_strokes_and_brightens_paper(self) -> None:
        """Faint pencil strokes should darken; white paper should stay bright."""
        from app.services.image_processing import enhance_text

        # White background with faint pencil-gray text strip (value ~130)
        img = Image.new("RGB", (200, 200), (255, 255, 255))
        for y in range(90, 110):
            for x in range(20, 180):
                img.putpixel((x, y), (130, 130, 130))  # faint pencil stroke
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        enhanced_bytes = enhance_text(buf.getvalue())
        enhanced = Image.open(io.BytesIO(enhanced_bytes))

        # Stroke area (130 gray) should become darker
        stroke_original = 130.0
        stroke_enhanced = sum(enhanced.getpixel((100, 100))) / 3
        assert stroke_enhanced < stroke_original, f"Stroke should darken: {stroke_enhanced} not < {stroke_original}"

        # Paper area (255 white) should stay bright (≥240)
        paper_enhanced = sum(enhanced.getpixel((10, 10))) / 3
        assert paper_enhanced >= 240, f"Paper should stay bright: {paper_enhanced} < 240"

    def test_process_image_pipeline(self) -> None:
        """process_image should return valid PNG bytes."""
        from app.services.image_processing import process_image

        img = Image.new("RGB", (100, 100), "white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        result = process_image(buf.getvalue())
        # Should be valid PNG
        result_img = Image.open(io.BytesIO(result))
        assert result_img.format == "PNG"


class TestProcessImageAI:
    """The AI-guided pipeline mocks the analyzer; no network calls."""

    def _png(self, w: int = 400, h: int = 500) -> bytes:
        return _make_png_with_content(w, h)

    def test_happy_path_applies_corners_and_enhance(self) -> None:
        from app.services.image_processing import process_image_ai

        analyzer = MagicMock()
        analyzer.analyze.return_value = {
            "is_document": True,
            "corners": [[20, 20], [380, 20], [380, 480], [20, 480]],
            "rotation_deg": 0.0,
            "enhance_params": {"clahe_clip": 2.5, "gamma": 1.0, "binarize": False},
            "confidence": 0.95,
        }
        out = process_image_ai(self._png(), preprocessor=analyzer)
        analyzer.analyze.assert_called_once()
        result = Image.open(io.BytesIO(out))
        assert result.format == "PNG"

    def test_invalid_corners_skip_warp_but_still_enhance(self) -> None:
        from app.services.image_processing import process_image_ai

        analyzer = MagicMock()
        # Corners covering <5% area — must be rejected, but enhance still runs.
        analyzer.analyze.return_value = {
            "is_document": True,
            "corners": [[0, 0], [10, 0], [10, 10], [0, 10]],
            "rotation_deg": 0.0,
            "enhance_params": {},
            "confidence": 0.3,
        }
        out = process_image_ai(self._png(), preprocessor=analyzer)
        result = Image.open(io.BytesIO(out))
        # Output is the unwarped enhanced image: same dimensions as input.
        assert result.width == 400
        assert result.height == 500

    def test_is_document_false_only_enhances(self) -> None:
        from app.services.image_processing import process_image_ai

        analyzer = MagicMock()
        analyzer.analyze.return_value = {
            "is_document": False,
            "corners": None,
            "rotation_deg": 0.0,
            "enhance_params": {"clahe_clip": 2.0},
            "confidence": 0.1,
        }
        out = process_image_ai(self._png(), preprocessor=analyzer)
        result = Image.open(io.BytesIO(out))
        assert result.width == 400
        assert result.height == 500

    def test_analyzer_exception_falls_back_to_opencv(self) -> None:
        from app.services.image_processing import process_image_ai

        analyzer = MagicMock()
        analyzer.analyze.side_effect = RuntimeError("OpenAI down")
        out = process_image_ai(self._png(), preprocessor=analyzer)
        # Fallback returns valid PNG — same as the OpenCV-only pipeline would.
        Image.open(io.BytesIO(out))


class TestAnalyzeFlag:
    """The /analyze endpoint switches pipelines based on USE_AI_PREPROCESSING."""

    def test_flag_off_uses_opencv_pipeline(
        self,
        client: TestClient,
        test_user: User,
        auth_headers: dict,
    ) -> None:
        png = _make_png_with_content()
        with (
            patch("app.api.pdf_generator.settings") as mock_settings,
            patch("app.api.pdf_generator.process_image") as mock_pi,
            patch("app.api.pdf_generator.process_image_ai") as mock_ai,
        ):
            mock_settings.USE_AI_PREPROCESSING = False
            mock_settings.AI_PREPROCESSING_CONCURRENCY = 6
            mock_settings.RATE_LIMIT_UPLOAD = "10/minute"
            mock_pi.return_value = png  # echo back as "processed"
            response = client.post(
                "/api/v1/pdf-generator/analyze",
                headers=auth_headers,
                files=[("files", ("a.png", png, "image/png"))],
            )
        assert response.status_code == 200, response.text
        assert mock_pi.called
        assert not mock_ai.called

    def test_flag_on_uses_ai_pipeline(
        self,
        client: TestClient,
        test_user: User,
        auth_headers: dict,
    ) -> None:
        png = _make_png_with_content()
        with (
            patch("app.api.pdf_generator.settings") as mock_settings,
            patch("app.api.pdf_generator.process_image") as mock_pi,
            patch("app.api.pdf_generator.process_image_ai") as mock_ai,
        ):
            mock_settings.USE_AI_PREPROCESSING = True
            mock_settings.AI_PREPROCESSING_CONCURRENCY = 6
            mock_settings.RATE_LIMIT_UPLOAD = "10/minute"
            mock_ai.return_value = png
            response = client.post(
                "/api/v1/pdf-generator/analyze",
                headers=auth_headers,
                files=[("files", ("a.png", png, "image/png"))],
            )
        assert response.status_code == 200, response.text
        assert mock_ai.called
        assert not mock_pi.called

    def test_runs_in_parallel(
        self,
        client: TestClient,
        test_user: User,
        auth_headers: dict,
    ) -> None:
        """4 photos × 100ms processing must finish in well under 400ms (serial)."""
        import time

        png = _make_png_with_content()

        def slow(_data: bytes) -> bytes:
            time.sleep(0.1)
            return png

        with (
            patch("app.api.pdf_generator.settings") as mock_settings,
            patch("app.api.pdf_generator.process_image_ai", side_effect=slow),
        ):
            mock_settings.USE_AI_PREPROCESSING = True
            mock_settings.AI_PREPROCESSING_CONCURRENCY = 6
            mock_settings.RATE_LIMIT_UPLOAD = "10/minute"
            start = time.perf_counter()
            response = client.post(
                "/api/v1/pdf-generator/analyze",
                headers=auth_headers,
                files=[("files", (f"a{i}.png", png, "image/png")) for i in range(4)],
            )
            elapsed = time.perf_counter() - start

        assert response.status_code == 200, response.text
        # 4 in parallel ≈ 100-150ms; serial would be >=400ms. Use 350 as buffer.
        assert elapsed < 0.35, f"Expected parallel execution, took {elapsed:.3f}s"


class TestGeneratePdf:
    def test_valid_generation(self, client: TestClient, db: None, test_user: User, auth_headers: dict) -> None:
        png_data = _make_png_bytes()
        response = client.post(
            "/api/v1/pdf-generator/generate",
            headers=auth_headers,
            files=[("files", ("page1.png", png_data, "image/png"))],
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        # PDF files start with %PDF
        assert response.content[:5] == b"%PDF-"

    def test_no_files(self, client: TestClient, auth_headers: dict) -> None:
        response = client.post(
            "/api/v1/pdf-generator/generate",
            headers=auth_headers,
            files=[],
        )
        assert response.status_code in (400, 422)

    def test_multiple_pages(self, client: TestClient, db: None, test_user: User, auth_headers: dict) -> None:
        png1 = _make_png_bytes(100, 100)
        png2 = _make_png_bytes(200, 300)
        response = client.post(
            "/api/v1/pdf-generator/generate",
            headers=auth_headers,
            files=[
                ("files", ("page1.png", png1, "image/png")),
                ("files", ("page2.png", png2, "image/png")),
            ],
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
