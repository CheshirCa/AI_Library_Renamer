import re
import logging
from typing import Optional, List

try:
    import pytesseract
    from PIL import Image
except ImportError:
    pytesseract = None
    Image = None

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------

def perform_ocr_image(img: 'Image.Image', lang: str = 'rus+eng') -> str:
    """Выполняет OCR для одного изображения."""
    if not pytesseract or not Image:
        return "Ошибка: pytesseract и pillow должны быть установлены для OCR."
    try:
        return pytesseract.image_to_string(img, lang=lang)
    except Exception as e:
        logger.error(f"Ошибка при OCR изображения: {e}")
        return f"Ошибка при OCR изображения: {str(e)}"


def perform_ocr_images(images: list, lang: str = 'rus+eng',
                       max_chars: Optional[int] = None) -> str:
    """OCR для списка изображений → нормализованный текст."""
    text_parts = []
    total_chars = 0

    for img in images:
        page_text = perform_ocr_image(img, lang)
        if page_text:
            text_parts.append(page_text)
            total_chars += len(page_text)
            if max_chars and total_chars >= max_chars:
                break

    raw = "\n".join(text_parts)
    normalized = normalize_ocr_text(raw)

    if max_chars:
        return normalized[:max_chars]
    return normalized


# ---------------------------------------------------------------------------
# Нормализация OCR-текста
# ---------------------------------------------------------------------------

def normalize_ocr_text(text: str) -> str:
    """
    Очищает и нормализует текст после Tesseract.

    Шаги:
      0. NFC Unicode — исправляет й/ё хранящиеся как два кодпоинта
      1. Склейка переносов:  "кни-\\nга" → "книга"
      2. Удаление мусорных строк (< 3 значимых символов)
      3. Нормализация пробелов внутри строк
      4. Схлопывание множественных пустых строк
      5. Сохранение дореформенных букв (ѣ, і, ѳ, ъ)
    """
    if not text:
        return text

    # 0. NFC нормализация
    import unicodedata
    text = unicodedata.normalize('NFC', text)

    # 1. Склейка переносов (дефис/тире в конце строки)
    text = re.sub(r'([а-яёА-ЯЁa-zA-Z])-\s*\n\s*([а-яёА-ЯЁa-zA-Z])', r'\1\2', text)

    lines = text.splitlines()
    cleaned: List[str] = []

    for line in lines:
        # 2. Убираем мусорные строки: меньше 3 буквенных символов
        alpha_count = sum(1 for c in line if c.isalpha())
        if alpha_count < 3 and len(line.strip()) > 0:
            # Исключение: строки только из цифр — номера страниц, годы
            if not re.match(r'^\s*\d{1,4}\s*$', line):
                continue

        # 3. Нормализация пробелов внутри строки
        line = re.sub(r'[ \t]{2,}', ' ', line).strip()
        cleaned.append(line)

    # 4. Схлопываем более двух подряд пустых строк
    result = '\n'.join(cleaned)
    result = re.sub(r'\n{3,}', '\n\n', result)

    return result.strip()


def extract_ocr_features(text: str) -> str:
    """
    Выделяет структурные признаки из OCR-текста для обогащения промпта.

    Возвращает строку с разделами:
      - Строки заглавными буквами (вероятные заголовки/авторы)
      - Даты и годы
      - Издательства и серии
      - Повторяющиеся фразы (колонтитулы)
      - Дореформенная орфография
    """
    lines = text.splitlines()
    features: List[str] = []

    # Строки написанные преимущественно заглавными (заголовки, авторы)
    caps_lines = [
        l.strip() for l in lines
        if len(l.strip()) > 4
        and sum(1 for c in l if c.isupper()) / max(len([c for c in l if c.isalpha()]), 1) > 0.6
    ]
    if caps_lines:
        features.append("Строки заглавными буквами (заголовки/авторы):")
        features.extend(f"  {l}" for l in caps_lines[:8])

    # Годы и даты
    years = re.findall(r'\b(1[5-9]\d\d|20[0-2]\d)\b', text)
    if years:
        unique_years = sorted(set(years))
        features.append(f"Годы: {', '.join(unique_years)}")

    # Издательства
    pub_patterns = re.findall(
        r'(?:изд(?:ательство)?\.?\s*[«"]?([А-ЯЁ][^\n»"]{2,30})|'
        r'([А-ЯЁ][а-яё]+(?:издат|пресс|Press|Publishing)[^\n]{0,20}))',
        text, re.IGNORECASE
    )
    pubs = [p[0] or p[1] for p in pub_patterns if any(p)]
    if pubs:
        features.append(f"Издательства: {'; '.join(pubs[:4])}")

    # Дореформенная орфография
    old_ortho = re.findall(r'[ѣіѳѵ]', text)
    if old_ortho:
        features.append("Дореформенная орфография — возможно дореволюционное издание")

    # Повторяющиеся фразы длиннее 4 слов (колонтитулы)
    phrases: dict = {}
    for line in lines:
        stripped = line.strip()
        word_count = len(stripped.split())
        if 4 <= word_count <= 10:
            phrases[stripped] = phrases.get(stripped, 0) + 1
    repeated = [p for p, cnt in phrases.items() if cnt >= 2]
    if repeated:
        features.append("Повторяющиеся строки (колонтитулы/заголовки глав):")
        features.extend(f"  {p}" for p in repeated[:5])

    return '\n'.join(features) if features else ""
