from app.services.dish_aliases import alias_groups, alias_variants
from app.services.native_retrieval import keyword_query_tokens


def test_aliases_are_bidirectional_and_exclude_self():
    assert "番茄" in alias_variants("西红柿")
    assert "西红柿" in alias_variants("番茄")
    assert "番茄" not in alias_variants("番茄")
    assert alias_variants("没有这道菜") == ()


def test_every_alias_survives_tokenization_as_one_term():
    # An alias that Jieba splits into pieces can never equal an indexed lexeme, so
    # it would silently widen nothing. Guard the table against dead entries.
    dead = [
        term
        for group in alias_groups()
        for term in group
        if keyword_query_tokens(term) != [term]
    ]
    assert dead == []


def test_alias_expansion_adds_the_library_wording():
    assert keyword_query_tokens("番茄炒蛋") == ["番茄", "炒蛋"]
    # The alias is appended, never substituted: the user's own wording still counts.
    assert keyword_query_tokens("西红柿炒鸡蛋") == ["西红柿", "炒鸡蛋"]
