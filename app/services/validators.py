import re
from app.config import settings

def validate_password(password: str) -> tuple[bool, str]:
    """Validate password meets security policy."""
    if len(password) < settings.PASSWORD_MIN_LENGTH:
        return False, f"La contrasena debe tener al menos {settings.PASSWORD_MIN_LENGTH} caracteres"
    if settings.PASSWORD_REQUIRE_UPPERCASE and not re.search(r"[A-Z]", password):
        return False, "La contrasena debe contener al menos una letra mayuscula"
    if settings.PASSWORD_REQUIRE_DIGIT and not re.search(r"\d", password):
        return False, "La contrasena debe contener al menos un numero"
    return True, ""

def validate_file_upload(filename: str, content_bytes: bytes) -> tuple[bool, str]:
    """Validate uploaded file by extension and magic bytes."""
    import os
    ext = os.path.splitext(filename)[1].lower() if filename else ""
    if ext not in settings.ALLOWED_UPLOAD_EXTENSIONS:
        return False, f"Tipo de archivo no permitido: {ext}"

    # Check magic bytes
    magic_bytes = {
        b"%PDF": [".pdf"],
        b"\x89PNG": [".png"],
        b"\xff\xd8\xff": [".jpg", ".jpeg"],
        b"GIF8": [".gif"],
        b"BM": [".bmp"],
        b"II\x2a\x00": [".tiff"],
        b"MM\x00\x2a": [".tiff"],
    }

    for magic, extensions in magic_bytes.items():
        if content_bytes[:len(magic)] == magic:
            if ext in extensions:
                return True, ""
            return False, f"El contenido del archivo no coincide con la extension {ext}"

    # If no magic byte match and extension is allowed, accept (some formats vary)
    return True, ""
