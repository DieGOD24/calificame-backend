import json
from typing import Any

from app.agents.base import BaseAgent

ENROLLMENT_SYSTEM_PROMPT = """Eres un asistente que extrae listas de estudiantes desde tablas planas (Excel o CSV) con formatos variados.

Recibirás el contenido completo de la tabla (filas de encabezado, metadatos, filas vacías y datos).
Tu tarea es identificar, para CADA estudiante listado, tres campos:

- "student_identifier": el número de documento, cédula, código estudiantil o matrícula. Debe ser solo dígitos/letras del identificador, sin prefijos como "CC" o "Doc:".
- "student_name": el nombre completo tal como aparece (sin cambiar mayúsculas).
- "student_email": la dirección de correo electrónico si existe. Si no hay, usa null.

## REGLAS
1. Ignora filas que sean títulos, metadatos (asignatura, docente, horario, fechas), encabezados o líneas en blanco.
2. Si un identificador aparece como número con coma decimal (ej. "1,116,434,602.00"), normalízalo a los dígitos ("1116434602").
3. Si una celda contiene varios valores separados por comas, toma solo el primero válido para email.
4. Si no hay email, devuelve null — nunca inventes correos.
5. No devuelvas filas duplicadas (mismo identificador).
6. No incluyas docentes ni coordinadores — solo estudiantes.

## FORMATO DE RESPUESTA
Devuelve SOLO un array JSON válido, sin markdown ni texto adicional. Ejemplo:

[
  {"student_identifier": "1116434602", "student_name": "ALVAREZ GUERRA SANTIAGO", "student_email": "s.alvarez5@utp.edu.co"},
  {"student_identifier": "1004720362", "student_name": "BAENA VELASQUEZ JUAN CAMILO", "student_email": null}
]

Si no logras identificar ningún estudiante, devuelve [].
"""


class EnrollmentExtractionAgent(BaseAgent):
    """Agent that parses variable student-roster formats into structured records."""

    def execute(self, **kwargs: Any) -> list[dict]:
        """Extract student rows from a flattened text representation of the roster.

        Args:
            table_text: Plain-text rendering of the CSV / XLSX (tab-separated cells).

        Returns:
            List of dicts with student_identifier, student_name, student_email.
        """
        table_text: str = kwargs.get("table_text", "")
        if not table_text.strip():
            return []

        # Guard: protect against huge files blowing the token budget
        if len(table_text) > 40_000:
            table_text = table_text[:40_000]

        user_text = (
            "Extrae la lista de estudiantes de esta tabla. Cada fila puede "
            "representar un estudiante. Devuelve un array JSON como se indicó.\n\n"
            f"TABLA:\n{table_text}"
        )

        raw = self._chat_completion(
            messages=[
                {"role": "system", "content": ENROLLMENT_SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            max_tokens=8192,
        )

        return self._parse_json_response(raw)

    @staticmethod
    def _parse_json_response(text: str) -> list[dict]:
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:])
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        try:
            data = json.loads(text)
            if isinstance(data, list):
                return [r for r in data if isinstance(r, dict)]
        except json.JSONDecodeError:
            pass

        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                if isinstance(data, list):
                    return [r for r in data if isinstance(r, dict)]
            except json.JSONDecodeError:
                pass
        return []
