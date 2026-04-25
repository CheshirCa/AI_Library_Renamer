import logging
from typing import Optional

try:
    import pytesseract
    from PIL import Image
except ImportError:
    pytesseract = None
    Image = None

logger = logging.getLogger(__name__)

def perform_ocr_image(img: 'Image.Image', lang: str = 'rus+eng') -> str:
    """Выполняет OCR для одного изображения"""
    if not pytesseract or not Image:
        return "Ошибка: pytesseract и pillow должны быть установлены для OCR."
    try:
        return pytesseract.image_to_string(img, lang=lang)
    except Exception as e:
        logger.error(f"Ошибка при OCR изображения: {e}")
        return f"Ошибка при OCR изображения: {str(e)}"

def perform_ocr_images(images: list, lang: str = 'rus+eng', max_chars: Optional[int] = None) -> str:
    """OCR для списка изображений с ограничением символов"""
    text_parts = []
    total_chars = 0

    for img in images:
        page_text = perform_ocr_image(img, lang)
        if page_text:
            text_parts.append(page_text)
            total_chars += len(page_text)
            if max_chars and total_chars >= max_chars:
                break

    full_text = "\n".join(text_parts)
    if max_chars:
        return full_text[:max_chars]
    return full_text
