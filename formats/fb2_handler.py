import xml.etree.ElementTree as ET
from .base_handler import BaseFormatHandler
from typing import Dict, Any

class FB2Handler(BaseFormatHandler):
    """Обработчик для FB2 файлов"""
    
    @staticmethod
    def can_handle(file_path: str) -> bool:
        return BaseFormatHandler.get_file_extension(file_path) == '.fb2'
    
    @staticmethod
    def extract_text(file_path: str, parameters: Dict[str, Any]) -> str:
        action_type = parameters.get('type', 'first_chars')
        amount = parameters.get('amount', 500)
        
        try:
            tree = ET.parse(file_path)
            root = tree.getroot()
            
            # namespace для FB2
            ns = {'fb': 'http://www.gribuser.ru/xml/fictionbook/2.0'}
            
            # Извлекаем заголовок
            title = root.find('.//fb:book-title', ns)
            title_text = title.text if title is not None else "Без названия"
            
            # Извлекаем основной текст
            body = root.find('.//fb:body', ns)
            text_content = []
            if body is not None:
                for elem in body.iter():
                    if elem.text and elem.tag.endswith('}p'):  # параграфы
                        text_content.append(elem.text)
            
            full_text = f"{title_text}\n\n" + "\n".join(text_content)
            
            if action_type == 'first_chars':
                return full_text[:amount]
            else:
                return full_text
                
        except ET.ParseError:
            # Если XML parsing fails, пробуем прочитать как plain text
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.read(amount)
            except Exception as e:
                return f"Ошибка при обработке FB2 файла: {str(e)}"
        except Exception as e:
            return f"Ошибка при обработке FB2 файла: {str(e)}"
