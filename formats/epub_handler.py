import os
import logging
import zipfile
import xml.etree.ElementTree as ET
from typing import Dict, Any
from .base_handler import BaseFormatHandler

logger = logging.getLogger(__name__)

class EPUBHandler(BaseFormatHandler):
    """Обработчик для EPUB файлов"""
    
    # Определяем пространства имен
    CONTAINER_NS = {'ct': 'urn:oasis:names:tc:opendocument:xmlns:container'}
    OPF_NS = {'opf': 'http://www.idpf.org/2007/opf'}
    DC_NS = {'dc': 'http://purl.org/dc/elements/1.1/'}
    
    @staticmethod
    def can_handle(file_path: str) -> bool:
        return BaseFormatHandler.get_file_extension(file_path).lower() == '.epub'
    
    @staticmethod
    def extract_text(file_path: str, parameters: Dict[str, Any]) -> str:
        action_type = parameters.get('type', 'first_chars')
        amount = parameters.get('amount', 1000)
        
        try:
            text_content = EPUBHandler._extract_epub_text(file_path, amount)
            
            if text_content and len(text_content.strip()) > 100:
                if action_type == 'first_chars':
                    return text_content[:amount]
                else:
                    return text_content
            
            return EPUBHandler._extract_epub_fallback(file_path, amount)
                
        except Exception as e:
            logger.error(f"Ошибка при обработке EPUB {file_path}: {e}")
            return f"Ошибка при обработке EPUB: {str(e)}"
    
    @staticmethod
    def _extract_epub_text(epub_path: str, max_chars: int = 5000) -> str:
        """Извлекает текст из EPUB файла"""
        text_parts = []
        total_chars = 0
        
        try:
            with zipfile.ZipFile(epub_path, 'r') as epub_zip:
                file_list = epub_zip.namelist()
                
                # Ищем container.xml
                container_path = next((f for f in file_list if f.endswith('container.xml')), None)
                
                if not container_path:
                    return EPUBHandler._extract_epub_fallback(epub_path, max_chars)
                
                # Читаем container.xml
                with epub_zip.open(container_path) as container_file:
                    container_tree = ET.parse(container_file)
                    rootfile = container_tree.find('.//ct:rootfile', EPUBHandler.CONTAINER_NS)
                    
                    if rootfile is None:
                        return EPUBHandler._extract_epub_fallback(epub_path, max_chars)
                    
                    opf_path = rootfile.get('full-path', '')
                    
                    if not opf_path:
                        return EPUBHandler._extract_epub_fallback(epub_path, max_chars)
                    
                    # Читаем OPF файл
                    with epub_zip.open(opf_path) as opf_file:
                        opf_tree = ET.parse(opf_file)
                        manifest = opf_tree.find('.//opf:manifest', EPUBHandler.OPF_NS)
                        
                        if not manifest:
                            return EPUBHandler._extract_epub_fallback(epub_path, max_chars)
                        
                        # Обрабатываем content файлы
                        for item in manifest.findall('opf:item', EPUBHandler.OPF_NS):
                            media_type = item.get('media-type', '')
                            href = item.get('href', '')
                            
                            if media_type in ['application/xhtml+xml', 'text/html']:
                                # Корректно формируем путь
                                content_dir = os.path.dirname(opf_path)
                                content_path = os.path.normpath(os.path.join(content_dir, href)) if content_dir else href
                                
                                if content_path not in file_list:
                                    continue
                                
                                try:
                                    with epub_zip.open(content_path) as content_file:
                                        try:
                                            # Пробуем парсить как XML
                                            content_tree = ET.parse(content_file)
                                            content_root = content_tree.getroot()
                                            
                                            # Рекурсивно извлекаем текст
                                            def extract_text_from_element(elem):
                                                nonlocal total_chars
                                                if total_chars >= max_chars:
                                                    return
                                                
                                                if elem.text and elem.text.strip():
                                                    text = elem.text.strip()
                                                    text_parts.append(text)
                                                    total_chars += len(text)
                                                
                                                for child in elem:
                                                    extract_text_from_element(child)
                                                    
                                                if elem.tail and elem.tail.strip():
                                                    text = elem.tail.strip()
                                                    text_parts.append(text)
                                                    total_chars += len(text)
                                            
                                            extract_text_from_element(content_root)
                                            
                                        except ET.ParseError:
                                            # Fallback: читаем как текст
                                            content_file.seek(0)
                                            text_content = content_file.read().decode('utf-8', errors='ignore')
                                            # Упрощенное удаление тегов
                                            import re
                                            clean_text = re.sub('<[^<]+?>', ' ', text_content)
                                            clean_text = re.sub('\s+', ' ', clean_text).strip()
                                            if clean_text:
                                                text_parts.append(clean_text)
                                                total_chars += len(clean_text)
                                                
                                except Exception as e:
                                    logger.debug(f"Не удалось прочитать файл {content_path}: {e}")
                                    continue
                                
                                if total_chars >= max_chars:
                                    break
            
            return " ".join(text_parts)[:max_chars]
            
        except Exception as e:
            logger.error(f"Ошибка при извлечении текста из EPUB: {e}")
            return EPUBHandler._extract_epub_fallback(epub_path, max_chars)
    
    @staticmethod
    def _extract_epub_fallback(epub_path: str, max_chars: int = 5000) -> str:
        """Простой fallback метод извлечения текста из EPUB"""
        try:
            with zipfile.ZipFile(epub_path, 'r') as epub_zip:
                text_parts = []
                total_chars = 0
                
                for file_name in epub_zip.namelist():
                    if file_name.endswith(('.xhtml', '.html', '.xml', '.txt', '.htm')):
                        try:
                            with epub_zip.open(file_name) as file:
                                content = file.read().decode('utf-8', errors='ignore')
                                import re
                                clean_text = re.sub('<[^<]+?>', ' ', content)
                                clean_text = re.sub('\s+', ' ', clean_text).strip()
                                
                                if clean_text and len(clean_text) > 50:
                                    text_parts.append(clean_text)
                                    total_chars += len(clean_text)
                                    
                                if total_chars >= max_chars:
                                    break
                                    
                        except Exception as e:
                            logger.debug(f"Не удалось обработать файл {file_name}: {e}")
                            continue
                
                result = ' '.join(text_parts)
                return result[:max_chars] if result else "Не удалось извлечь текст из EPUB"
                
        except Exception as e:
            return f"Ошибка при fallback обработке EPUB: {str(e)}"
    
    @staticmethod
    def get_metadata(file_path: str) -> Dict[str, str]:
        """Извлекает метаданные из EPUB"""
        try:
            metadata = {}
            
            with zipfile.ZipFile(file_path, 'r') as epub_zip:
                # Ищем OPF файл
                opf_path = next((f for f in epub_zip.namelist() if f.endswith('.opf')), None)
                
                if not opf_path:
                    return {}
                
                with epub_zip.open(opf_path) as opf_file:
                    opf_tree = ET.parse(opf_file)
                    metadata_elem = opf_tree.find('.//opf:metadata', EPUBHandler.OPF_NS)
                    
                    if not metadata_elem:
                        return {}
                    
                    # Объединяем пространства имен для поиска
                    ns = {**EPUBHandler.OPF_NS, **EPUBHandler.DC_NS}
                    
                    def get_meta_value(tag):
                        element = metadata_elem.find(f'.//dc:{tag}', ns)
                        if element is not None and element.text:
                            return element.text.strip()
                        return ''
                    
                    metadata = {
                        'title': get_meta_value('title'),
                        'author': get_meta_value('creator'),
                        'publisher': get_meta_value('publisher'),
                        'date': get_meta_value('date'),
                        'language': get_meta_value('language'),
                        'identifier': get_meta_value('identifier'),
                        'description': get_meta_value('description'),
                        'subject': get_meta_value('subject')
                    }
            
            return {k: v for k, v in metadata.items() if v}
            
        except Exception as e:
            logger.error(f"Ошибка при извлечении метаданных EPUB: {e}")
            return {}
    
    @staticmethod
    def get_content_structure(file_path: str) -> Dict[str, Any]:
        """Возвращает структуру содержимого EPUB"""
        try:
            structure = {
                'chapters': [],
                'toc': [],
                'files': []
            }
            
            with zipfile.ZipFile(file_path, 'r') as epub_zip:
                structure['files'] = epub_zip.namelist()
                
                opf_path = next((f for f in epub_zip.namelist() if f.endswith('.opf')), None)
                
                if opf_path:
                    with epub_zip.open(opf_path) as opf_file:
                        opf_tree = ET.parse(opf_file)
                        manifest = opf_tree.find('.//opf:manifest', EPUBHandler.OPF_NS)
                        
                        if manifest:
                            for item in manifest.findall('opf:item', EPUBHandler.OPF_NS):
                                media_type = item.get('media-type', '')
                                href = item.get('href', '')
                                id_attr = item.get('id', '')
                                
                                if media_type in ['application/xhtml+xml', 'text/html']:
                                    structure['chapters'].append({
                                        'id': id_attr,
                                        'file': href,
                                        'media_type': media_type
                                    })
            
            return structure
            
        except Exception as e:
            logger.error(f"Ошибка при получении структуры EPUB: {e}")
            return {'error': str(e)}
