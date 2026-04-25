import zipfile
import tempfile
import os
from .base_handler import BaseFormatHandler
from typing import Dict, Any

class ZIPHandler(BaseFormatHandler):
    """Обработчик для ZIP архивов"""
    
    @staticmethod
    def can_handle(file_path: str) -> bool:
        return BaseFormatHandler.get_file_extension(file_path) in ['.zip', '.rar']
    
    @staticmethod
    def extract_text(file_path: str, parameters: Dict[str, Any]) -> str:
        # Для архивов мы не извлекаем текст, а сообщаем о содержимом
        try:
            if file_path.lower().endswith('.zip'):
                with zipfile.ZipFile(file_path, 'r') as zip_ref:
                    files = zip_ref.namelist()
                    return f"ZIP архив содержит: {', '.join(files[:5])}" + ("..." if len(files) > 5 else "")
            else:
                return "RAR архив (требуется дополнительная обработка)"
        except Exception as e:
            return f"Ошибка при обработке архива: {str(e)}"
