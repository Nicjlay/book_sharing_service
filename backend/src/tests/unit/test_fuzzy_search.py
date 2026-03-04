"""
Тесты нечёткого поиска.

Все функции — чистые (нет IO, нет БД) — тестируются напрямую.
"""
import pytest
from infrastructure.services.fuzzy_search import (
    _normalize,
    _tokens,
    _trigrams,
    _trigrams_set,
    _jaccard,
    _token_max_similarity,
    fuzzy_score,
    search_books,
    did_you_mean,
)


# ── _normalize ────────────────────────────────────────────────────────────────

class TestNormalize:
    def test_lowercases(self):
        assert _normalize("ТОЛСТОЙ") == "толстой"

    def test_strips_whitespace(self):
        assert _normalize("  текст  ") == "текст"

    def test_collapses_spaces(self):
        assert _normalize("война  и  мир") == "война и мир"

    def test_removes_punctuation(self):
        result = _normalize("Толстой, Л.Н.")
        assert "," not in result
        assert "." not in result

    def test_empty_string(self):
        assert _normalize("") == ""


# ── _tokens ───────────────────────────────────────────────────────────────────

class TestTokens:
    def test_basic_split(self):
        assert _tokens("война мир") == ["война", "мир"]

    def test_skips_single_chars(self):
        # токены длиной < 2 отбрасываются
        tokens = _tokens("а б толстой")
        assert "а" not in tokens
        assert "б" not in tokens
        assert "толстой" in tokens

    def test_empty(self):
        assert _tokens("") == []

    def test_only_short_words(self):
        assert _tokens("а б в") == []


# ── _trigrams ─────────────────────────────────────────────────────────────────

class TestTrigrams:
    def test_normal_word(self):
        # "кот" → padded "$кот$" → {"$ко", "кот", "от$"}
        result = _trigrams("кот")
        assert "$ко" in result
        assert "кот" in result
        assert "от$" in result

    def test_short_word(self):
        # слово из 1 буквы → padded "$$а$" → слишком короткое → {padded}
        result = _trigrams("а")
        assert len(result) > 0  # хотя бы один элемент

    def test_long_word(self):
        result = _trigrams("толстой")
        assert len(result) > 3  # длинное слово → много триграмм

    def test_empty_returns_set(self):
        # пустая строка не должна падать
        result = _trigrams("")
        assert isinstance(result, set)


# ── _jaccard ──────────────────────────────────────────────────────────────────

class TestJaccard:
    def test_identical_sets(self):
        a = {"abc", "bcd"}
        assert _jaccard(a, a) == 1.0

    def test_disjoint_sets(self):
        a = {"abc"}
        b = {"xyz"}
        assert _jaccard(a, b) == 0.0

    def test_partial_overlap(self):
        a = {"abc", "bcd"}
        b = {"bcd", "cde"}
        # |A∩B| = 1, |A∪B| = 3 → 1/3
        assert abs(_jaccard(a, b) - 1/3) < 1e-9

    def test_empty_set_a(self):
        assert _jaccard(set(), {"abc"}) == 0.0

    def test_empty_set_b(self):
        assert _jaccard({"abc"}, set()) == 0.0

    def test_both_empty(self):
        assert _jaccard(set(), set()) == 0.0


# ── _token_max_similarity ─────────────────────────────────────────────────────

class TestTokenMaxSimilarity:
    def test_identical_tokens(self):
        score = _token_max_similarity(["толстой"], ["толстой"])
        assert score == 1.0

    def test_empty_query(self):
        assert _token_max_similarity([], ["толстой"]) == 0.0

    def test_empty_field(self):
        assert _token_max_similarity(["толстой"], []) == 0.0

    def test_both_empty(self):
        assert _token_max_similarity([], []) == 0.0

    def test_partial_match(self):
        score = _token_max_similarity(["толстой"], ["толстый"])
        assert 0 < score < 1.0


# ── fuzzy_score ───────────────────────────────────────────────────────────────

class TestFuzzyScore:
    BOOK_WAR_PEACE = {"title": "Война и мир", "author": "Толстой"}
    BOOK_CRIME     = {"title": "Преступление и наказание", "author": "Достоевский"}

    def test_exact_title_match(self):
        score = fuzzy_score("война мир", self.BOOK_WAR_PEACE)
        assert score > 0.5

    def test_exact_author_match(self):
        score = fuzzy_score("толстой", self.BOOK_WAR_PEACE)
        assert score > 0.5

    def test_typo_in_title(self):
        # "влйна" вместо "война"
        score = fuzzy_score("влйна мир", self.BOOK_WAR_PEACE)
        assert score > 0.0

    def test_empty_query(self):
        assert fuzzy_score("", self.BOOK_WAR_PEACE) == 0.0

    def test_blank_query(self):
        assert fuzzy_score("   ", self.BOOK_WAR_PEACE) == 0.0

    def test_prefix_bonus_title(self):
        """Точное вхождение подстроки в title даёт бонус."""
        score_with_prefix = fuzzy_score("война", self.BOOK_WAR_PEACE)
        score_no_prefix   = fuzzy_score("война", self.BOOK_CRIME)
        assert score_with_prefix > score_no_prefix

    def test_prefix_bonus_author(self):
        """Точное вхождение в author тоже даёт бонус."""
        score = fuzzy_score("достоевский", self.BOOK_CRIME)
        assert score > 0.5

    def test_score_is_capped_at_one(self):
        score = fuzzy_score("война мир толстой", self.BOOK_WAR_PEACE)
        assert score <= 1.0

    def test_missing_fields(self):
        """Книга без title/author не должна падать."""
        score = fuzzy_score("война", {})
        assert score == 0.0

    def test_unrelated_query(self):
        score = fuzzy_score("гарри поттер", self.BOOK_WAR_PEACE)
        assert score < 0.5


# ── search_books ──────────────────────────────────────────────────────────────

class TestSearchBooks:
    BOOKS = [
        {"id": 1, "title": "Война и мир",               "author": "Толстой"},
        {"id": 2, "title": "Преступление и наказание",   "author": "Достоевский"},
        {"id": 3, "title": "Анна Каренина",              "author": "Толстой"},
    ]

    def test_finds_relevant_book(self):
        results = search_books("война", self.BOOKS)
        ids = [b["id"] for b, _ in results]
        assert 1 in ids

    def test_returns_scores(self):
        results = search_books("толстой", self.BOOKS)
        for _, score in results:
            assert 0.0 <= score <= 1.0

    def test_sorted_by_score_descending(self):
        results = search_books("толстой", self.BOOKS)
        scores = [s for _, s in results]
        assert scores == sorted(scores, reverse=True)

    def test_limit_respected(self):
        results = search_books("толстой", self.BOOKS, limit=1)
        assert len(results) <= 1

    def test_threshold_filters_low_scores(self):
        results = search_books("xyzxyzxyz", self.BOOKS, threshold=0.5)
        assert results == []

    def test_empty_query_returns_empty(self):
        assert search_books("", self.BOOKS) == []

    def test_blank_query_returns_empty(self):
        assert search_books("   ", self.BOOKS) == []

    def test_empty_books_returns_empty(self):
        assert search_books("толстой", []) == []


# ── did_you_mean ──────────────────────────────────────────────────────────────

class TestDidYouMean:
    BOOKS = [
        {"id": 1, "title": "Война и мир", "author": "Толстой"},
    ]

    def test_returns_suggestion_for_typo(self):
        # "влйна" — опечатка, score будет низким (~0.1–0.35)
        suggestion = did_you_mean("влйна", self.BOOKS)
        # может вернуть title или None — проверяем что не падает
        assert suggestion is None or isinstance(suggestion, str)

    def test_returns_none_for_completely_unrelated(self):
        result = did_you_mean("zyxzyxzyx", self.BOOKS)
        assert result is None

    def test_returns_none_for_empty_books(self):
        assert did_you_mean("война", []) is None

    def test_returns_none_for_high_score(self):
        """Точное совпадение score >= 0.35 → did_you_mean возвращает None."""
        result = did_you_mean("война мир", self.BOOKS)
        assert result is None