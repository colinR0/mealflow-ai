"""Local embedding adapter tests; the real model is never loaded."""

from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.services.local_embedding import LocalEmbedding
from app.services.retrieval_gateway import configured_embedder


class FakeTransformer:
    instances = []

    def __init__(self, model_name, *, device=None, local_files_only=False):
        self.model_name = model_name
        self.device = device
        self.local_files_only = local_files_only
        self.max_seq_length = None
        self.inputs = []
        self.__class__.instances.append(self)

    def encode(self, texts, **kwargs):
        self.inputs.extend(texts)
        return [[1.0, 2.0, 2.0] for _ in texts]


@pytest.fixture
def fake_transformer(monkeypatch):
    FakeTransformer.instances.clear()
    monkeypatch.setitem(
        __import__("sys").modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeTransformer),
    )
    return FakeTransformer


def test_documents_and_queries_use_different_bge_instructions(fake_transformer):
    embedding = LocalEmbedding(
        model_name="local-bge-test",
        dimensions=3,
        query_prefix="query: ",
        device="cpu",
        batch_size=2,
    )
    document = embedding.embed_document("番茄炒蛋")
    query = embedding.embed_query("番茄炒蛋")

    assert document == pytest.approx([1 / 3, 2 / 3, 2 / 3])
    assert query == document
    assert fake_transformer.instances[0].inputs == ["番茄炒蛋", "query: 番茄炒蛋"]
    assert fake_transformer.instances[0].device == "cpu"


def test_dimension_mismatch_is_rejected(fake_transformer):
    embedding = LocalEmbedding(model_name="local-bge-test", dimensions=4)
    with pytest.raises(ValueError, match="dimension mismatch"):
        embedding.embed_document("番茄")


def test_missing_optional_dependency_has_actionable_error(monkeypatch):
    import builtins

    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "sentence_transformers":
            raise ImportError("not installed")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    embedding = LocalEmbedding(model_name="local-bge-test", dimensions=3)
    with pytest.raises(RuntimeError, match="local-embeddings"):
        embedding.embed_document("番茄")


def test_configured_embedder_builds_real_provider_without_loading(fake_transformer):
    settings = Settings(
        _env_file=None,
        retrieval_mode="native",
        retrieval_embedding_model="local-bge-test",
        retrieval_embedding_dimensions=3,
        retrieval_embedding_device="cpu",
        retrieval_embedding_query_prefix="query: ",
    )
    provider = configured_embedder(settings)
    assert provider.fake is False
    assert provider.model == "local-bge-test"
    assert provider.dimensions == 3
    assert provider.embed_query("番茄") == pytest.approx([1 / 3, 2 / 3, 2 / 3])
    assert fake_transformer.instances[-1].inputs == ["query: 番茄"]