import os
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any

logger = logging.getLogger(__name__)

class BaseFormatHandler(ABC):
    """Базовый класс для обработчиков различных форматов файлов"""
    
    @staticmethod
    @abstractmethod
    def can_handle(file_path: str) -> bool:
        """Проверяет, может ли обработчик работать с данным файлом"""
        pass
    
    @staticmethod
    @abstractmethod
    def extract_text(file_path: str, parameters: Dict[str, Any]) -> str:
        """Извлекает текст из файла согласно параметрам"""
        pass
    
    @staticmethod
    def get_file_extension(file_path: str) -> str:
        """Возвращает расширение файла в нижнем регистре"""
        return os.path.splitext(file_path)[1].lower()
