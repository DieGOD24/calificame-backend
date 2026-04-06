import json
from typing import Any

from app.agents.base import BaseAgent
from app.models.question import Question

GRADING_SYSTEM_PROMPT = """Eres un profesor experto calificando examenes y talleres. Se te mostrara el examen de un estudiante junto con las preguntas y respuestas correctas del solucionario.

## INSTRUCCIONES DE CALIFICACION

Para CADA pregunta debes:

1. **Localizar** la respuesta del estudiante en la(s) imagen(es) del examen.
2. **Extraer** textualmente lo que el estudiante escribio (incluyendo calculos, pasos, tablas).
3. **Comparar** contra la respuesta correcta del solucionario.
4. **Evaluar** segun el tipo de pregunta:

### Opcion Multiple
- Respuesta exacta = puntaje completo
- Respuesta incorrecta = 0 puntos

### Desarrollo / Procedimiento
- Procedimiento correcto Y resultado correcto = puntaje completo
- Procedimiento correcto pero error de calculo = 50-80% del puntaje
- Procedimiento parcialmente correcto = 20-60% del puntaje
- Solo resultado sin procedimiento (si se pide procedimiento) = 30% del puntaje
- Resultado incorrecto sin procedimiento = 0 puntos

### Tablas (frecuencia, datos, etc.)
- Compara celda por celda cuando sea posible
- Errores menores de redondeo = descuento minimo
- Estructura correcta con valores incorrectos = puntaje parcial

### Conceptuales
- Evalua la idea central, no las palabras exactas
- Respuesta completa y precisa = puntaje completo
- Respuesta parcial = puntaje proporcional

5. **Asignar** un puntaje de 0 a max_points.
6. **Dar feedback** especifico: que hizo bien, que fallo, cual era lo correcto.
7. **Confianza**: que tan seguro estas de haber leido correctamente la respuesta (0.0 a 1.0).

## FORMATO DE RESPUESTA

Array JSON con objetos:
- "question_id": string (el ID proporcionado)
- "extracted_answer": string (lo que el estudiante escribio, transcrito fielmente)
- "is_correct": boolean (true si obtuvo puntaje completo)
- "score": float (0 a max_points, permite decimales para puntaje parcial)
- "feedback": string (explicacion breve en espanol de la calificacion)
- "confidence": float (0.0 a 1.0, confianza en la lectura de la respuesta)

Devuelve SOLO el array JSON, sin texto adicional."""


class GradingAgent(BaseAgent):
    """Agent specialized in grading student exams against answer keys."""

    def execute(self, **kwargs: Any) -> list[dict]:
        """Grade a student exam against the answer key.

        Args:
            student_images: List of image bytes from the student exam.
            questions: List of Question model instances.
            config: Project configuration dict.

        Returns:
            List of grading result dicts per question.
        """
        student_images: list[bytes] = kwargs.get("student_images", [])
        questions: list[Question] = kwargs.get("questions", [])
        config: dict = kwargs.get("config", {})

        if not student_images or not questions:
            return []

        # Build the questions reference for the AI
        questions_ref = []
        for q in questions:
            questions_ref.append(
                {
                    "question_id": q.id,
                    "question_number": q.question_number,
                    "question_text": q.question_text or "",
                    "correct_answer": q.correct_answer,
                    "max_points": q.points or 1.0,
                }
            )

        type_labels = {
            "multiple_choice": "opcion multiple",
            "open_ended": "respuesta abierta/desarrollo",
            "mixed": "mixto",
        }
        exam_type = config.get("exam_type", "mixed")
        additional = config.get("additional_instructions", "")

        user_text = (
            f"Califica este examen de estudiante.\n"
            f"Tipo de examen: {type_labels.get(exam_type, exam_type)}\n\n"
            f"Preguntas y respuestas correctas del solucionario:\n"
            f"{json.dumps(questions_ref, indent=2, ensure_ascii=False)}\n\n"
        )
        if additional:
            user_text += f"Instrucciones del profesor: {additional}\n\n"

        user_text += (
            "Analiza la(s) imagen(es) del examen del estudiante y califica "
            "cada pregunta comparando con el solucionario."
        )

        raw_result = self._chat_completion_with_images(
            system_prompt=GRADING_SYSTEM_PROMPT,
            user_text=user_text,
            images=student_images,
            max_tokens=16384,
        )

        results = self._parse_json_response(raw_result)

        # Ensure all questions have a result
        found_ids = {r["question_id"] for r in results if "question_id" in r}
        for q in questions:
            if q.id not in found_ids:
                results.append(
                    {
                        "question_id": q.id,
                        "extracted_answer": "",
                        "is_correct": False,
                        "score": 0.0,
                        "feedback": "No se encontro la respuesta del estudiante en el examen.",
                        "confidence": 0.0,
                    }
                )

        return results

    @staticmethod
    def _parse_json_response(text: str) -> list[dict]:
        """Parse a JSON array response, handling markdown code fences."""
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
                return data
        except json.JSONDecodeError:
            pass

        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass

        return []
