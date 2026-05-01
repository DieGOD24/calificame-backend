"""Tests for student enrollment parsing: heuristic parser + AI fallback."""

import asyncio
import io
from typing import Any
from unittest.mock import patch

import openpyxl
from fastapi.testclient import TestClient

from app.models.clase import Class, ClassEnrollment
from app.services.enrollment import _parse_xlsx, flatten_to_text, parse_student_file


def _make_xlsx(rows: list[list[Any]]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class _FakeUpload:
    def __init__(self, filename: str, data: bytes):
        self.filename = filename
        self._data = data

    async def read(self) -> bytes:
        return self._data


class TestHeuristicParser:
    def test_parses_utp_roster_with_metadata_header_at_row_10(self) -> None:
        """The real UTP roster has 10 metadata rows before the header."""
        rows: list[list[Any]] = [
            ["", None, None, "Listado de Estudiantes con Informacion Basica", None, None, None, None],
            [None] * 8,
            [None] * 8,
            [None, None, None, "04/03/2026", None, None, None, None],
            [None] * 8,
            ["Asignatura:IS512-ESTADISTICA- Grupo: 101", None, None, None, None, None, "", None],
            ["Docente:37556937-MARTHA ASCENCIO MENDOZA", None, None, None, None, None, "", None],
            [""] + [None] * 7,
            ["Horario: / (AULA:4B-201)", None, None, None, None, None, None, None],
            ["Documento", "Nombres", "Telefono", None, "Celular", "EMAIL", None, "Cursos ILEX aprobado"],
            [
                "1116434602",
                "ALVAREZ GUERRA SANTIAGO",
                "3154697216",
                None,
                "3209897592",
                "s.alvarez5@utp.edu.co",
                None,
                "3",
            ],
            [
                "1004720362",
                "BAENA VELASQUEZ JUAN CAMILO",
                "3300690",
                None,
                "3225984622",
                "j.baena@utp.edu.co",
                None,
                "3",
            ],
            [None] * 8,
            [
                "1233898369",
                "BARRIOS GONZALEZ LIZETH JULIANA",
                "Sin Telefono",
                None,
                "3043329170",
                "l.barrios@utp.edu.co",
                None,
                "3",
            ],
        ]
        content = _make_xlsx(rows)
        records = _parse_xlsx(content)

        assert len(records) == 3
        identifiers = {r["student_identifier"] for r in records}
        assert identifiers == {"1116434602", "1004720362", "1233898369"}
        first = next(r for r in records if r["student_identifier"] == "1116434602")
        assert first["student_name"] == "ALVAREZ GUERRA SANTIAGO"
        assert first["student_email"] == "s.alvarez5@utp.edu.co"

    def test_parses_simple_headers_at_row_0(self) -> None:
        rows = [
            ["codigo", "nombre", "email"],
            ["A001", "Ana Maria", "ana@example.com"],
            ["A002", "Luis Perez", None],
        ]
        records = _parse_xlsx(_make_xlsx(rows))
        assert len(records) == 2
        assert records[0]["student_identifier"] == "A001"
        assert records[1]["student_email"] is None

    def test_returns_empty_when_no_header_match(self) -> None:
        """If no header row is recognizable, return [] so caller can try AI."""
        rows = [
            ["Col1", "Col2", "Col3"],
            ["x", "y", "z"],
        ]
        records = _parse_xlsx(_make_xlsx(rows))
        assert records == []

    def test_normalizes_numeric_identifiers(self) -> None:
        """Excel often stores IDs as numbers -> strings come back as '1116434602.0'."""
        rows = [
            ["documento", "nombre", "email"],
            [1116434602, "Santiago Lopez", "s.lopez@utp.edu.co"],
        ]
        records = _parse_xlsx(_make_xlsx(rows))
        assert len(records) == 1
        assert records[0]["student_identifier"] == "1116434602"

    def test_csv_via_parse_student_file(self) -> None:
        content = b"codigo,nombre,email\nA001,Ana,ana@x.com\nA002,Luis,luis@x.com\n"
        upload = _FakeUpload("roster.csv", content)
        records = asyncio.run(parse_student_file(upload))  # type: ignore[arg-type]
        assert len(records) == 2


class TestFlattenToText:
    def test_flatten_xlsx_includes_all_rows(self) -> None:
        rows = [["Documento", "Nombres", "EMAIL"], ["123", "X Y", "x@y.com"]]
        content = _make_xlsx(rows)
        text = flatten_to_text(content, "r.xlsx")
        assert "Documento" in text
        assert "123" in text
        assert "x@y.com" in text


class TestBulkEnrollEndpoint:
    def test_bulk_enroll_with_utp_format(
        self,
        client: TestClient,
        test_class: Class,
        auth_headers: dict,
    ) -> None:
        """End-to-end: upload UTP-style xlsx, verify enrollments are created."""
        rows: list[list[Any]] = [
            ["", None, None, "Listado de Estudiantes", None, None, None, None],
            [None] * 8,
            ["Documento", "Nombres", "Telefono", None, "Celular", "EMAIL", None, "x"],
            ["1116434602", "ALVAREZ GUERRA SANTIAGO", "3154697216", None, "", "s.alvarez5@utp.edu.co", None, ""],
            ["1004720362", "BAENA VELASQUEZ JUAN CAMILO", "", None, "", "j.baena@utp.edu.co", None, ""],
        ]
        # Put headers at row 2 so we also exercise metadata-skip.
        # Pad to >=10 rows of metadata by shifting:
        padded = [rows[0], rows[1]] + rows[2:]
        content = _make_xlsx(padded)

        response = client.post(
            f"/api/v1/classes/{test_class.id}/enrollments/bulk",
            headers=auth_headers,
            files={
                "file": ("roster.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["added"] == 2
        assert data["skipped"] == 0
        assert data["used_ai"] is False

    def test_bulk_enroll_falls_back_to_ai_when_heuristic_fails(
        self,
        client: TestClient,
        db,
        test_class: Class,
        auth_headers: dict,
    ) -> None:
        """When headers don't match, the endpoint invokes EnrollmentExtractionAgent."""
        # File with unrecognizable column names so heuristic returns [].
        rows = [
            ["Col1", "Col2", "Col3"],
            ["999", "Juan", "juan@x.com"],
            ["1000", "Maria", "maria@x.com"],
        ]
        content = _make_xlsx(rows)

        with (
            patch("app.agents.enrollment_extraction_agent.EnrollmentExtractionAgent.execute") as mock_execute,
            patch("app.api.classes.settings") as mock_settings,
        ):
            mock_settings.OPENAI_API_KEY = "sk-test"
            mock_settings.MAX_FILE_SIZE_MB = 50
            mock_settings.RATE_LIMIT_UPLOAD = "10/minute"
            mock_execute.return_value = [
                {"student_identifier": "999", "student_name": "Juan", "student_email": "juan@x.com"},
                {"student_identifier": "1000", "student_name": "Maria", "student_email": "maria@x.com"},
            ]

            response = client.post(
                f"/api/v1/classes/{test_class.id}/enrollments/bulk",
                headers=auth_headers,
                files={
                    "file": ("weird.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                },
            )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["added"] == 2
        assert data["used_ai"] is True
        enrollments = db.query(ClassEnrollment).filter(ClassEnrollment.class_id == test_class.id).all()
        assert {e.student_identifier for e in enrollments} == {"999", "1000"}

    def test_bulk_enroll_rejects_empty_file(
        self,
        client: TestClient,
        test_class: Class,
        auth_headers: dict,
    ) -> None:
        response = client.post(
            f"/api/v1/classes/{test_class.id}/enrollments/bulk",
            headers=auth_headers,
            files={"file": ("empty.csv", b"", "text/csv")},
        )
        assert response.status_code == 400

    def test_bulk_enroll_no_ai_key_returns_helpful_error(
        self,
        client: TestClient,
        test_class: Class,
        auth_headers: dict,
    ) -> None:
        """If parser fails and no API key exists, message should mention columns."""
        rows = [["X", "Y", "Z"], ["a", "b", "c"]]
        content = _make_xlsx(rows)
        with patch("app.api.classes.settings") as mock_settings:
            mock_settings.OPENAI_API_KEY = ""
            mock_settings.MAX_FILE_SIZE_MB = 50
            mock_settings.RATE_LIMIT_UPLOAD = "10/minute"
            response = client.post(
                f"/api/v1/classes/{test_class.id}/enrollments/bulk",
                headers=auth_headers,
                files={
                    "file": ("bad.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                },
            )
        assert response.status_code == 400
        assert "columna" in response.json()["detail"].lower()
