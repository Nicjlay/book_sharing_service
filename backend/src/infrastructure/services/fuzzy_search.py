"""
Нечёткий триграммный поиск книг.

Алгоритм:
  1. Разбиваем строку на триграммы (3-буквенные подстроки): "толстой" → {"тол","олс","лст","сто","той"}
  2. Считаем коэффициент сходства Жаккара между множествами триграмм запроса и поля книги:
       similarity = |A ∩ B| / |A ∪ B|
  3. Берём максимум по всем словам запроса vs всем словам поля (title + author).
  4. Возвращаем книги у которых score > порога, отсортированные по убыванию score.

Это даёт устойчивость к:
  - опечаткам ("толстой" → "толтой", "tlstoy")
  - перестановке слов ("война мир" → "мир война")
  - частичному вводу ("толст" найдёт "Толстой")
"""
from typing import List, Dict, Tuple
import re
import unicodedata


# ---------------------------------------------------------------------------
# Нормализация
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Приводим к нижнему регистру, убираем диакритику и лишние символы."""
    text = text.lower().strip()
    # Нормализация unicode (ё → е и т.п. для надёжности)
    text = unicodedata.normalize("NFKD", text)
    # Оставляем только буквы, цифры и пробелы
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tokens(text: str) -> List[str]:
    """Разбиваем нормализованный текст на слова."""
    return [t for t in _normalize(text).split() if len(t) >= 2]


# ---------------------------------------------------------------------------
# Триграммы
# ---------------------------------------------------------------------------

def _trigrams(word: str) -> set:
    """
    Строим множество триграмм для слова.
    Добавляем паддинг-символы чтобы учитывать начало/конец:
      "кот" → {"$ко", "кот", "от$"}
    """
    padded = f"${word}$"
    if len(padded) < 3:
        return {padded}
    return {padded[i:i+3] for i in range(len(padded) - 2)}


def _trigrams_set(text: str) -> set:
    """Триграммы всего текста (объединение по всем словам)."""
    result = set()
    for token in _tokens(text):
        result |= _trigrams(token)
    return result


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------

def _jaccard(a: set, b: set) -> float:
    """Коэффициент Жаккара: |A ∩ B| / |A ∪ B|"""
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union


def _token_max_similarity(query_tokens: List[str], field_tokens: List[str]) -> float:
    """
    Для каждого токена запроса ищем наилучшее совпадение среди токенов поля.
    Итоговый score = среднее лучших совпадений.
    """
    if not query_tokens or not field_tokens:
        return 0.0

    field_trigrams = [_trigrams(t) for t in field_tokens]
    scores = []

    for qt in query_tokens:
        qt_tri = _trigrams(qt)
        best = max(_jaccard(qt_tri, ft) for ft in field_trigrams)
        scores.append(best)

    return sum(scores) / len(scores)


# ---------------------------------------------------------------------------
# Основная функция
# ---------------------------------------------------------------------------

def fuzzy_score(query: str, book: Dict) -> float:
    """
    Возвращает score [0.0, 1.0] того насколько книга соответствует запросу.
    Проверяет title и author, берёт максимум.
    """
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0.0

    title_tokens  = _tokens(book.get("title", ""))
    author_tokens = _tokens(book.get("author", ""))

    title_score  = _token_max_similarity(query_tokens, title_tokens)  if title_tokens  else 0.0
    author_score = _token_max_similarity(query_tokens, author_tokens) if author_tokens else 0.0

    # Небольшой бонус за точное вхождение подстроки (prefix-match)
    norm_query = _normalize(query)
    bonus = 0.0
    if norm_query in _normalize(book.get("title", "")):
        bonus = 0.25
    elif norm_query in _normalize(book.get("author", "")):
        bonus = 0.20

    return min(1.0, max(title_score, author_score) + bonus)


def search_books(
    query: str,
    books: List[Dict],
    threshold: float = 0.20,
    limit: int = 10
) -> List[Tuple[Dict, float]]:
    """
    Нечёткий поиск по списку книг.

    Args:
        query:     строка запроса от пользователя
        books:     список словарей книг (должны содержать "title", "author")
        threshold: минимальный score для включения в результат (0.0–1.0)
        limit:     максимальное число результатов

    Returns:
        Список пар (book_dict, score), отсортированных по убыванию score.
    """
    if not query.strip() or not books:
        return []

    scored = [(book, fuzzy_score(query, book)) for book in books]
    scored = [(b, s) for b, s in scored if s >= threshold]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]


# ---------------------------------------------------------------------------
# Подсказка "вы имели в виду?"
# ---------------------------------------------------------------------------

def did_you_mean(query: str, books: List[Dict]) -> str | None:
    """
    Если score лучшего результата невысокий — возвращаем предложение
    с названием наиболее похожей книги (для UX-подсказки).
    """
    results = search_books(query, books, threshold=0.10, limit=1)
    if not results:
        return None
    best_book, best_score = results[0]
    # Предлагаем подсказку только если нет явно хорошего совпадения
    if 0.10 <= best_score < 0.35:
        return best_book.get("title") or best_book.get("author")
    return None
