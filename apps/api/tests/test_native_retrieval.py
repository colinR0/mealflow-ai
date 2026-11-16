import pytest

from app.services.hybrid_retrieval import RetrievalHit, fake_embedding
from app.services.native_retrieval import (
    keyword_query,
    keyword_query_tokens,
    keyword_term_groups,
    keyword_terms,
    rrf_hits,
    tokenize_chinese,
    vector_literal,
)


def test_native_helpers_mark_sql_and_fake_boundaries():
    assert "番茄" in tokenize_chinese("番茄鸡蛋")
    assert vector_literal([0, 1.5]) == "[0.0,1.5]"
    hits = [RetrievalHit("recipe", "chunk", 1, 1, "text")]
    assert rrf_hits(hits, [], 1)[0][0] == "recipe"
    assert len(fake_embedding("测试")) == 64


@pytest.mark.parametrize(("query", "tsquery"), [
    # plainto_tsquery joined with AND, so this used to match nothing.
    ("红烧肉的做法和步骤", "红烧肉"),
    # Aliases widen the OR expression so colloquial names reach library wording.
    ("番茄炒蛋", "番茄 | 西红柿 | 蕃茄 | 炒蛋"),
    # Quantities describe a dish; they must not seed the keyword channel.
    ("我想买一个垃圾桶", "垃圾桶"),
    ("冰箱里有鸡蛋", "冰箱 | 鸡蛋"),
])
def test_keyword_query_builds_or_expression(query, tsquery):
    assert keyword_query(query) == (tsquery, True)


def test_keyword_term_groups_count_aliases_as_one_term():
    # Coverage must not count three aliases of the same term as three matches.
    groups = keyword_term_groups("番茄炒蛋")
    assert groups == [["番茄", "西红柿", "蕃茄"], ["炒蛋"]]


def test_keyword_query_keeps_unrelated_terms_untouched():
    assert keyword_query("红烧肉") == ("红烧肉", True)
    assert keyword_terms("红烧肉") == ["红烧肉"]


@pytest.mark.parametrize("query", ["的了吗呢", "", "   ", "!!!", "推荐个好吃的"])
def test_keyword_query_disables_channel_without_terms(query):
    assert keyword_query(query) == ("_nomatch_", False)


@pytest.mark.parametrize(("query", "tokens"), [
    # A single character identifies a dish only when it is the whole query.
    ("虾", ["虾"]),
    ("葱", ["葱"]),
    # tsquery operators and quotes are stripped, never executed.
    ("红烧肉&", ["红烧肉"]),
    ("a'b", ["a", "b"]),
    ("红烧肉<->鸡蛋", ["红烧肉", "鸡蛋"]),
])
def test_keyword_query_tokens_keeps_short_food_and_strips_syntax(query, tokens):
    assert keyword_query_tokens(query) == tokens


@pytest.mark.parametrize("values", [[], [True], [False, 1], [float("nan")],
                                   [float("inf")], [-float("inf")], ["1"]])
def test_vector_literal_rejects_invalid_values(values):
    with pytest.raises(ValueError, match="finite"):
        vector_literal(values)
