"""Offline English-to-Spanish query translation.

Deterministic, dependency-free word substitution against a small bundled
lexicon. No network calls and no third-party runtime dependency, matching
the rest of this project's offline-only design.
"""

_EN_TO_ES = {
    "a": "un",
    "all": "todo",
    "and": "y",
    "are": "son",
    "book": "libro",
    "chapter": "capitulo",
    "code": "codigo",
    "computer": "computadora",
    "data": "datos",
    "day": "dia",
    "file": "archivo",
    "for": "para",
    "from": "de",
    "good": "bueno",
    "guide": "guia",
    "hello": "hola",
    "how": "como",
    "in": "en",
    "introduction": "introduccion",
    "is": "es",
    "network": "red",
    "new": "nuevo",
    "no": "no",
    "of": "de",
    "one": "uno",
    "page": "pagina",
    "programming": "programacion",
    "search": "busqueda",
    "system": "sistema",
    "test": "prueba",
    "the": "el",
    "this": "esto",
    "to": "a",
    "two": "dos",
    "with": "con",
    "world": "mundo",
    "yes": "si",
}


def translate_to_spanish(text: str) -> str:
    """Translate ``text`` to Spanish via whitespace-tokenized word lookup.

    Each whitespace-separated word is looked up case-insensitively in a
    bundled English-Spanish lexicon. Words with no known translation are
    returned unchanged. Word order is preserved.
    """

    words = text.split()
    translated = [_EN_TO_ES.get(word.lower(), word) for word in words]
    return " ".join(translated)
