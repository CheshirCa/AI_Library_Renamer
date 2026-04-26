"""
text_utils.py — утилиты для нормализации текста и имён файлов.

  normalize_unicode(text)    — NFC-нормализация: исправляет й/ё/ъ хранящиеся
                               как два кодпоинта (артефакт macOS/некоторых редакторов)
  translit_to_russian(text)  — преобразует транслит в кириллицу
  fix_filename(name)         — полная нормализация имени файла
"""

import re
import unicodedata


# ---------------------------------------------------------------------------
# Unicode NFC нормализация
# ---------------------------------------------------------------------------

def normalize_unicode(text: str) -> str:
    """
    Приводит строку к NFC-форме.

    Проблема: й может быть записана как и + U+0306 (combining breve),
    что в некоторых ФС и приложениях отображается как "и ̆" или "Устрои ̆ства".
    NFC объединяет базовый символ и диакритику в один кодпоинт.
    """
    return unicodedata.normalize('NFC', text)


# ---------------------------------------------------------------------------
# Транслитерация → кириллица
# ---------------------------------------------------------------------------

# Таблица замен: длинные последовательности в первую очередь
_TRANSLIT_TABLE = [
    # Трёхбуквенные — в первую очередь
    ('shh', 'щ'), ('Shh', 'Щ'), ('SHH', 'Щ'),
    ('sch', 'щ'), ('Sch', 'Щ'), ('SCH', 'Щ'),

    # Двухбуквенные
    ('zh',  'ж'), ('Zh',  'Ж'), ('ZH',  'Ж'),
    ('sh',  'ш'), ('Sh',  'Ш'), ('SH',  'Ш'),
    ('ch',  'ч'), ('Ch',  'Ч'), ('CH',  'Ч'),
    ('ts',  'ц'), ('Ts',  'Ц'), ('TS',  'Ц'),
    ('tz',  'ц'), ('Tz',  'Ц'), ('TZ',  'Ц'),
    ('yu',  'ю'), ('Yu',  'Ю'), ('YU',  'Ю'),
    ('ya',  'я'), ('Ya',  'Я'), ('YA',  'Я'),
    ('yo',  'ё'), ('Yo',  'Ё'), ('YO',  'Ё'),
    ('ye',  'е'), ('Ye',  'Е'), ('YE',  'Е'),
    ('kh',  'х'), ('Kh',  'Х'), ('KH',  'Х'),
    ('gh',  'г'), ('Gh',  'Г'),
    # Финальное -y после гласных = й (Tolstoy→Толстой, Shebes→Шебес нет, но Tchaikovsky→Чайковский)
    ('ay',  'ай'), ('Ay', 'Ай'), ('AY', 'АЙ'),
    ('ey',  'ей'), ('Ey', 'Ей'), ('EY', 'ЕЙ'),
    ('iy',  'ий'), ('Iy', 'Ий'), ('IY', 'ИЙ'),
    ('oy',  'ой'), ('Oy', 'Ой'), ('OY', 'ОЙ'),
    ('uy',  'уй'), ('Uy', 'Уй'), ('UY', 'УЙ'),
    # двойное y в конце = ый/ий
    ('yy',  'ый'), ('Yy', 'Ый'), ('YY', 'ЫЙ'),
    # ck → к
    ('ck',  'к'), ('CK',  'К'),
    # ykh/ikh → ых/их (Elektricheskikh → Электрических)
    ('ykh', 'ых'), ('Ykh', 'Ых'), ('YKH', 'ЫХ'),
    ('ikh', 'их'), ('Ikh', 'Их'), ('IKH', 'ИХ'),
    # eln → лн (Elektro → Электро needs Э not Е)
    ('El',  'Эл'), ('EL',  'ЭЛ'),
    # согласная + y → согласная + ю (компьютер, бюро)
    ('py', 'пю'), ('Py', 'Пю'), ('by', 'бю'), ('By', 'Бю'),
    ('my', 'мю'), ('My', 'Мю'), ('ny', 'ню'), ('Ny', 'Ню'),
    ('vy', 'вю'), ('Vy', 'Вю'), ('fy', 'фю'), ('Fy', 'Фю'),

    # Однобуквенные
    ('a',   'а'), ('A',   'А'),
    ('b',   'б'), ('B',   'Б'),
    ('v',   'в'), ('V',   'В'),
    ('g',   'г'), ('G',   'Г'),
    ('d',   'д'), ('D',   'Д'),
    ('e',   'е'), ('E',   'Е'),
    ('z',   'з'), ('Z',   'З'),
    ('i',   'и'), ('I',   'И'),
    ('j',   'й'), ('J',   'Й'),
    ('k',   'к'), ('K',   'К'),
    ('l',   'л'), ('L',   'Л'),
    ('m',   'м'), ('M',   'М'),
    ('n',   'н'), ('N',   'Н'),
    ('o',   'о'), ('O',   'О'),
    ('p',   'п'), ('P',   'П'),
    ('r',   'р'), ('R',   'Р'),
    ('s',   'с'), ('S',   'С'),
    ('t',   'т'), ('T',   'Т'),
    ('u',   'у'), ('U',   'У'),
    ('f',   'ф'), ('F',   'Ф'),
    ('h',   'х'), ('H',   'Х'),
    ('c',   'с'), ('C',   'С'),  # С чаще чем К (1С, Цвет→Сvet)
    ('q',   'к'), ('Q',   'К'),
    ('w',   'в'), ('W',   'В'),
    ('x',   'кс'),('X',   'Кс'),
    ('y',   'й'), ('Y',   'Й'),  # одиночный y → й (не ы: в именах чаще так)
    ("'",   'ъ'),
]

# Слова-исключения, которые не надо транслитерировать
_SKIP_WORDS = {
    'pdf', 'djvu', 'epub', 'mobi', 'doc', 'docx', 'rtf', 'txt',
    'zip', 'rar', '7z', 'sql', 'xml', 'html', 'css', 'api',
    'isbn', 'url', 'ocr', 'llm',
}


# Сильные маркеры транслита — редки в английских словах
_STRONG_RU = re.compile(
    # Диграфы почти не встречающиеся в английском
    r'zh|shh|sch|kh|'
    # Начальные русские слоги (ya/yu/yo/ye в начале слова)
    r'\bya|\byu|\byo|\bye|\bts|'
    # Двойное y — типично для транслита прилагательных (novyy, kompyuternyy)
    r'yy|'
    # Суффиксы русских слов/фамилий — достаточно специфичны
    r'iya\b|iye\b|ikh\b|'            # -ия, -ие, -их
    r'ov\b|ev\b|'                    # -ов, -ев (Ivanov, Lebedev)
    r'sky\b|ski\b|skiy\b|skaya\b|'  # -ский/-ская (Tolstoy не отсюда, но Dostoevsky да)
    r'aya\b|oye\b|uyu\b|'           # падежные окончания прилагательных
    r'shch',                         # щ в ГОСТ транслите
    re.IGNORECASE
)

# Английские маркеры — при наличии НЕ транслитерируем
_ENGLISH = re.compile(
    r'\b(?:'
    # Артикли, предлоги, союзы — стопроцентный признак английского
    r'the|and|for|with|from|this|that|into|onto|upon|'
    r'a\b|an\b|in\b|of\b|to\b|by\b|at\b|on\b|'
    # Английские суффиксы
    r'\w+ing\b|\w+tion\b|\w+ness\b|\w+ment\b|\w+ful\b|\w+less\b|\w+able\b|\w+ible\b|'
    # IT и книжная лексика
    r'book|classroom|release|edition|volume|press|publishing|'
    r'computing|programming|software|hardware|network|security|'
    r'introduction|guide|handbook|manual|journal|library|'
    r'fundamentals|principles|elements|advanced|basic|applied|'
    r'english|humor|history|modern|ancient|practical|'
    r'university|institute|academy|college|school|'
    r'chapter|part|appendix|index|table|figure'
    r')', re.IGNORECASE
)


def _phrase_is_translit(phrase: str) -> bool:
    """
    Определяет является ли фраза целиком русским транслитом.
    Работает на уровне всей строки, а не отдельных слов.
    """
    # Нормализуем разделители перед анализом (подчёркивания → пробелы)
    phrase = phrase.replace('_', ' ')

    has_cyrillic = bool(re.search(r'[а-яёА-ЯЁ]', phrase))
    has_latin    = bool(re.search(r'[a-zA-Z]', phrase))

    if not has_latin or has_cyrillic:
        return False

    # Явный английский → нет
    if _ENGLISH.search(phrase):
        return False

    # Сильный сигнал русского транслита → да
    if _STRONG_RU.search(phrase):
        return True

    # Слабый сигнал: sh/ch/oy/ov/ev без типично английских паттернов
    weak_ru = bool(re.search(r'sh|ch|oy\b|ov\b|ev\b', phrase, re.IGNORECASE))
    english_common = bool(re.search(
        r'\b(?:fish|show|wash|wish|rich|much|such|which|'
        r'each|reach|teach|beach|touch|match|catch|watch|'
        r'ship|shop|shot|shut|she|her|his|him|'
        r'boy|toy|joy|enjoy|employ|annoy|convoy|'
        r'love|above|move|prove|over|cover|discover|hover|'
        r'novel|government|movement|development|'
        r'book|look|cook|took|hook|shook)\b',
        phrase, re.IGNORECASE
    ))
    if weak_ru and not english_common:
        return True

    return False


def translit_to_russian(text: str) -> str:
    """
    Преобразует транслитерированный текст в кириллицу.

    Сначала оценивает всю фразу — если это транслит, применяет замены пословно.
    Если это английский текст — возвращает без изменений.
    """
    # Если кириллицы больше 20% — текст уже русский
    cyrillic = sum(1 for c in text if '\u0400' <= c <= '\u04FF')
    if cyrillic / max(len(text), 1) > 0.2:
        return text

    # Проверяем всю фразу
    if not _phrase_is_translit(text):
        return text

    # Фраза — транслит. Применяем замены пословно.
    tokens = re.split(r'([\s\-_\.]+)', text)
    result = []
    for token in tokens:
        if re.match(r'^[\s\-_\.]+$', token):
            result.append(token)
        elif token.lower() in _SKIP_WORDS:
            result.append(token)
        else:
            r = token
            for lat, cyr in _TRANSLIT_TABLE:
                r = r.replace(lat, cyr)
            result.append(r)
    return ''.join(result)


def translit_word(word: str) -> str:
    """Совместимость: транслитерирует одно слово если вся фраза — транслит."""
    return translit_to_russian(word)


# ---------------------------------------------------------------------------
# Комплексная нормализация имени файла
# ---------------------------------------------------------------------------

def fix_filename(name: str, apply_translit: bool = True) -> str:
    """
    Полная нормализация имени файла:
      1. NFC Unicode — исправляет й/ё/ъ-артефакты
      2. Транслит → кириллица (если apply_translit=True)
      3. Нормализация подчёркиваний → пробелы
      4. Нормализация множественных пробелов
    """
    # 1. NFC
    name = normalize_unicode(name)

    if apply_translit:
        stem, ext = '', ''
        dot = name.rfind('.')
        if dot > 0:
            stem, ext = name[:dot], name[dot:]
        else:
            stem, ext = name, ''

        # Сначала транслит — потом специальные замены
        stem = translit_to_russian(stem)

        # Специальный случай: 1C → 1С (бренд, после транслита чтобы не мешать детектору)
        stem = re.sub(r'\b1C\b', '1С', stem)

        name = stem + ext

    # 3. Подчёркивания → пробелы
    name = name.replace('_', ' ')

    # 4. Множественные пробелы
    name = re.sub(r' {2,}', ' ', name).strip()

    return name
