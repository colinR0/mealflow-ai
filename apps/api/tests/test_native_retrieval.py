import pytest

from app.services.hybrid_retrieval import RetrievalHit, fake_embedding
from app.services.native_retrieval import (
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


@pytest.mark.parametrize("values", [[], [True], [False, 1], [float("nan")],
                                   [float("inf")], [-float("inf")], ["1"]])
def test_vector_literal_rejects_invalid_values(values):
    with pytest.raises(ValueError, match="finite"):
        vector_literal(values)
