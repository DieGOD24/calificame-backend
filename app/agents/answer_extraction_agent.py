import json
from typing import Any

from app.agents.base import BaseAgent

EXTRACTION_SYSTEM_PROMPT = """Eres un experto en analisis de documentos academicos. Tu tarea es extraer TODAS las preguntas/ejercicios y sus respuestas correctas de un solucionario de examen o taller.

## INSTRUCCIONES CRITICAS

1. **Analiza cada pagina imagen por imagen** con extremo cuidado. No omitas nada.
2. **Identifica CADA pregunta, ejercicio, problema o literal** que aparezca en el documento.
3. **Extrae la respuesta/solucion COMPLETA** incluyendo:
   - Procedimientos paso a paso
   - Formulas utilizadas con sus sustituciones
   - Calculos intermedios
   - Tablas de datos o frecuencias
   - Graficos descritos textualmente
   - Resultados finales con unidades
4. **Maneja sub-preguntas**: Si una pregunta tiene literales (a, b, c) o pasos numerados, incluyelos TODOS como parte de la respuesta de esa pregunta.
5. **Texto manuscrito**: Si hay texto escrito a mano, haz tu mejor esfuerzo por leerlo. Si no es legible, indica "[texto ilegible]".
6. **Formulas matematicas**: Escribe las formulas de forma clara. Ejemplo: "n_i = 1 + 3.32 * log(n)" en lugar de simbolos ambiguos.
7. **Tablas**: Reproduce tablas como texto estructurado. Ejemplo:
   "| Clase | fi | Fi | fr |
    | 10-15 | 3  | 3  | 0.15 |"

## TIPOS DE DOCUMENTOS QUE PUEDES ENCONTRAR

- **Examenes de opcion multiple**: Extrae la letra correcta Y el texto de la opcion.
- **Examenes de desarrollo**: Extrae el procedimiento completo y el resultado.
- **Talleres de estadistica**: Tablas de frecuencia, calculos de rango, intervalos, histogramas, etc.
- **Talleres de matematicas**: Ecuaciones, derivadas, integrales, graficas, etc.
- **Talleres de ciencias**: Diagramas, reacciones, explicaciones, etc.
- **Cuestionarios conceptuales**: Definiciones, explicaciones, comparaciones.

## FORMATO DE RESPUESTA

Devuelve un array JSON con objetos que tengan estos campos:
- "question_number": numero entero de la pregunta (1, 2, 3...)
- "question_text": el enunciado de la pregunta tal como aparece en el documento. Si solo dice "Ejercicio 1" o "Pregunta 1" sin mas texto, escribe lo que se pide contextualmente.
- "correct_answer": la solucion/respuesta COMPLETA. Incluye TODOS los pasos, calculos, tablas y el resultado final. Para respuestas largas, usa saltos de linea (\\n) para separar pasos.

## EJEMPLO DE SALIDA

```json
[
  {
    "question_number": 1,
    "question_text": "Determinar el Rango de los datos: 12, 15, 18, 22, 25, 30",
    "correct_answer": "Rango = Valor maximo - Valor minimo\\nRango = 30 - 12\\nRango = 18"
  },
  {
    "question_number": 2,
    "question_text": "Calcular el numero de intervalos usando la formula de Sturges",
    "correct_answer": "Formula: k = 1 + 3.32 * log(n)\\nn = 30 (cantidad de datos)\\nk = 1 + 3.32 * log(30)\\nk = 1 + 3.32 * 1.477\\nk = 1 + 4.9\\nk = 5.9 ≈ 6 intervalos"
  }
]
```

IMPORTANTE: Devuelve SOLO el array JSON, sin texto adicional ni bloques de codigo markdown."""


class AnswerExtractionAgent(BaseAgent):
    """Agent specialized in extracting Q&A pairs from exam answer key images."""

    def execute(self, **kwargs: Any) -> list[dict]:
        """Extract questions and answers from answer key images.

        Args:
            images: List of image bytes from the answer key.
            config: Project configuration dict.

        Returns:
            List of dicts with question_number, question_text, correct_answer.
        """
        images: list[bytes] = kwargs.get("images", [])
        config: dict = kwargs.get("config", {})

        if not images:
            return []

        exam_type = config.get("exam_type", "mixed")
        total_questions = config.get("total_questions")
        additional = config.get("additional_instructions", "")

        # Build context message
        type_labels = {
            "multiple_choice": "opcion multiple",
            "open_ended": "respuesta abierta/desarrollo",
            "mixed": "mixto (opcion multiple y desarrollo)",
        }
        user_text = (
            f"Analiza este solucionario de examen/taller.\n"
            f"Tipo de examen: {type_labels.get(exam_type, exam_type)}.\n"
        )
        if total_questions:
            user_text += f"Se esperan {total_questions} preguntas/ejercicios.\n"
        if additional:
            user_text += f"Instrucciones adicionales del profesor: {additional}\n"

        user_text += (
            "\nExtrae TODAS las preguntas con sus respuestas/soluciones COMPLETAS. "
            "No resumas ni abrevies las respuestas. Incluye cada paso, calculo y resultado."
        )

        # Send all images in a single request for full context
        raw_result = self._chat_completion_with_images(
            system_prompt=EXTRACTION_SYSTEM_PROMPT,
            user_text=user_text,
            images=images,
            max_tokens=16384,
        )

        result = self._parse_json_response(raw_result)

        if not result:
            return []

        # Validation pass: if expected count differs significantly, retry
        if total_questions and len(result) != total_questions:
            validation_prompt = (
                f"Encontre {len(result)} preguntas pero se esperan {total_questions}. "
                "Reanaliza el solucionario con mas cuidado. "
                "Busca preguntas que puedan estar en la misma pagina, sub-literales "
                "que deberian ser preguntas separadas, o preguntas que se me escaparon.\n\n"
                f"Mi extraccion actual:\n{json.dumps(result, ensure_ascii=False)}\n\n"
                "Devuelve el array JSON corregido y completo."
            )
            raw_validation = self._chat_completion_with_images(
                system_prompt=EXTRACTION_SYSTEM_PROMPT,
                user_text=validation_prompt,
                images=images,
                max_tokens=16384,
            )
            validated = self._parse_json_response(raw_validation)
            if validated and abs(len(validated) - total_questions) < abs(len(result) - total_questions):
                result = validated

        return result

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

        # Try to find JSON array within the text
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
