import os
import logging
from typing import Dict, Any
from .base_handler import BaseFormatHandler

logger = logging.getLogger(__name__)


class PDFHandler(BaseFormatHandler):
    """Обработчик для PDF файлов (pymupdf → PyPDF2 → OCR)"""

    @staticmethod
    def can_handle(file_path: str) -> bool:
        return BaseFormatHandler.get_file_extension(file_path) == ".pdf"

    @staticmethod
    def extract_text(file_path: str, parameters: Dict[str, Any]) -> str:
        action_type = parameters.get("type", "first_chars")
        amount      = parameters.get("amount", 500)

        # --- Попытка 1: pymupdf ---
        try:
            import fitz
            text = PDFHandler._extract_with_fitz(fitz, file_path, action_type, amount)
            if text:
                return text
            logger.info(f"pymupdf: текстового слоя нет в {os.path.basename(file_path)}, пробуем OCR")
        except ImportError:
            logger.warning("pymupdf не установлен, пробуем PyPDF2...")
        except Exception as e:
            logger.warning(f"pymupdf ошибка: {e}, пробуем PyPDF2...")

        # --- Попытка 2: PyPDF2 ---
        try:
            import PyPDF2
            text = PDFHandler._extract_with_pypdf2(PyPDF2, file_path, action_type, amount)
            if text:
                return text
            logger.info(f"PyPDF2: текстового слоя нет, пробуем OCR")
        except ImportError:
            logger.warning("PyPDF2 не установлен")
        except Exception as e:
            logger.warning(f"PyPDF2 ошибка: {e}")

        # --- Попытка 3: OCR через pdf2image + tesseract ---
        return PDFHandler._ocr_pdf(file_path, amount, action_type)

    @staticmethod
    def _extract_with_fitz(fitz, file_path: str, action_type: str, amount: int) -> str:
        doc   = fitz.open(file_path)
        parts = []
        total = 0
        for page in doc:
            t = page.get_text()
            if t.strip():
                parts.append(t)
                total += len(t)
            if action_type == "first_chars" and total >= amount:
                break
        doc.close()
        combined = "\n".join(parts)
        return (combined[:amount] if action_type == "first_chars" else combined) if combined.strip() else ""

    @staticmethod
    def _extract_with_pypdf2(PyPDF2, file_path: str, action_type: str, amount: int) -> str:
        text = ""
        with open(file_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                t = page.extract_text() or ""
                text += t + "\n"
                if action_type == "first_chars" and len(text) >= amount:
                    break
        result = text.strip()
        return text[:amount] if (result and action_type == "first_chars") else (text if result else "")

    @staticmethod
    def _ocr_pdf(file_path: str, amount: int, action_type: str) -> str:
        """OCR через pdf2image (требует poppler) + pytesseract."""
        try:
            from pdf2image import convert_from_path
            from .ocr_utils import perform_ocr_images
        except ImportError as e:
            return f"OCR недоступен: {e}. Установите pytesseract и poppler."

        try:
            # На Windows pdf2image ищет pdftoppm в PATH или poppler_path
            poppler_path = PDFHandler._find_poppler()
            kwargs = {"first_page": 1, "last_page": 3, "dpi": 150}
            if poppler_path:
                kwargs["poppler_path"] = poppler_path

            images = convert_from_path(file_path, **kwargs)
            max_chars = amount if action_type == "first_chars" else None
            return perform_ocr_images(images, lang="rus+eng", max_chars=max_chars)

        except Exception as e:
            logger.error(f"OCR PDF не удался: {e}")
            return ""   # возвращаем пустую строку, а не текст ошибки

    @staticmethod
    def _find_poppler() -> str:
        """
        Ищет poppler на Windows в типичных местах установки.
        Возвращает путь к папке bin или пустую строку (тогда pdf2image ищет сам).
        """
        import shutil
        if shutil.which("pdftoppm"):
            return ""  # уже в PATH

        candidates = [
            r"C:\gnuwin32\poppler\bin",
            r"C:\poppler\Library\bin",
            r"C:\poppler\bin",
            r"C:\Program Files\poppler\bin",
            r"C:\Program Files\poppler\Library\bin",
            r"C:\tools\poppler\Library\bin",
        ]
        for path in candidates:
            if os.path.isfile(os.path.join(path, "pdftoppm.exe")):
                logger.info(f"Найден poppler: {path}")
                return path

        logger.warning(
            "poppler не найден. OCR для PDF недоступен.\n"
            "Скачайте poppler: https://github.com/oschwartz10612/poppler-windows/releases\n"
            "Распакуйте и добавьте папку bin в PATH, или положите в C:\\poppler\\Library\\bin"
        )
        return ""

    @staticmethod
    def get_metadata(file_path: str) -> Dict[str, str]:
        try:
            import fitz
            doc = fitz.open(file_path)
            raw = doc.metadata
            doc.close()
            keys_map = {
                "title": "title", "author": "author", "subject": "subject",
                "creator": "creator", "producer": "producer",
                "creationDate": "creation_date", "modDate": "modification_date",
            }
            return {v: raw[k] for k, v in keys_map.items() if raw.get(k)}
        except Exception:
            try:
                import PyPDF2
                with open(file_path, "rb") as f:
                    m = PyPDF2.PdfReader(f).metadata or {}
                return {k.lstrip("/"): str(v) for k, v in m.items() if v}
            except Exception:
                return {}
