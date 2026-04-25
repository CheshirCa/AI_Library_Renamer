import os
import logging
from typing import Dict, Any
try:
    from PIL import Image
    import pytesseract
except ImportError:
    Image = None
    pytesseract = None

from .base_handler import BaseFormatHandler

logger = logging.getLogger(__name__)

class ImageHandler(BaseFormatHandler):
    """Обработчик для изображений с OCR"""
    
    IMAGE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.gif', '.webp']
    
    @staticmethod
    def can_handle(file_path: str) -> bool:
        ext = BaseFormatHandler.get_file_extension(file_path)
        return ext in ImageHandler.IMAGE_EXTENSIONS
    
    @staticmethod
    def extract_text(file_path: str, parameters: Dict[str, Any]) -> str:
        if Image is None or pytesseract is None:
            return "Ошибка: pytesseract и pillow должны быть установлены для OCR изображений. Установите: pip install pytesseract pillow"

        action_type = parameters.get('type', 'first_chars')
        amount = parameters.get('amount', 500)
        
        try:
            with Image.open(file_path) as img:
                # Масштабирование больших изображений
                max_dim = 3000
                if max(img.size) > max_dim:
                    scale = max_dim / max(img.size)
                    new_size = (int(img.size[0]*scale), int(img.size[1]*scale))
                    img = img.resize(new_size, Image.Resampling.LANCZOS)
                    logger.debug(f"Изображение масштабировано до {new_size}")
                
                # Поддержка OCR на русском и английском
                text = pytesseract.image_to_string(img, lang='rus+eng')
                
                if action_type == 'first_chars':
                    return text[:amount]
                else:
                    return text
        except Exception as e:
            logger.error(f"Ошибка при OCR изображения {file_path}: {e}")
            return f"Ошибка при OCR: {str(e)}"
    
    @staticmethod
    def get_metadata(file_path: str) -> Dict[str, str]:
        """Метаданных у изображений нет, возвращаем пустой словарь"""
        return {}
