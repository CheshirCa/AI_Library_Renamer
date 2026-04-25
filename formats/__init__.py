from typing import Dict
import logging

HANDLERS = []

def _register_handlers():
    """Ленивая регистрация обработчиков"""
    from .txt_handler   import TXTHandler
    from .pdf_handler   import PDFHandler
    from .docx_handler  import DOCXHandler
    from .fb2_handler   import FB2Handler
    from .djvu_handler  import DJVUHandler
    from .mobi_handler  import MOBIHandler
    from .zip_handler   import ZIPHandler
    from .epub_handler  import EPUBHandler
    from .image_handler import ImageHandler

    global HANDLERS
    HANDLERS = [
        TXTHandler,
        PDFHandler,
        DOCXHandler,
        FB2Handler,
        DJVUHandler,
        MOBIHandler,
        ZIPHandler,
        EPUBHandler,
        ImageHandler,
    ]


def get_handler_for_file(file_path: str):
    if not HANDLERS:
        _register_handlers()
    for handler in HANDLERS:
        if handler.can_handle(file_path):
            return handler
    from .base_handler import BaseFormatHandler
    return BaseFormatHandler


def extract_text_data(file_path: str, parameters: dict) -> str:
    handler = get_handler_for_file(file_path)
    return handler.extract_text(file_path, parameters)


def get_file_metadata(file_path: str) -> Dict[str, str]:
    handler = get_handler_for_file(file_path)
    if hasattr(handler, "get_metadata"):
        return handler.get_metadata(file_path)
    return {}
