import os
from .base_handler import BaseFormatHandler
from typing import Dict, Any

class TXTHandler(BaseFormatHandler):
    """Обработчик для TXT файлов"""
    
    @staticmethod
    def can_handle(file_path: str) -> bool:
        return BaseFormatHandler.get_file_extension(file_path) == '.txt'
    
    @staticmethod
    def extract_text(file_path: str, parameters: Dict[str, Any]) -> str:
        action_type = parameters.get('type', 'first_chars')
        amount = parameters.get('amount', 500)
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                if action_type == 'first_chars':
                    return f.read(amount)
                else:
                    # Для TXT first_pages не имеет смысла, возвращаем первые символы
                    return f.read(amount)
        except Exception as e:
            return f"Ошибка при чтении TXT файла: {str(e)}"
