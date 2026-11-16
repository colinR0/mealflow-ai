from app.services.text_chunking import split_document


def count(text: str) -> int:
    return len(text)


def test_recipe_chunks_keep_title_and_step_order():
    content = {
        "name": "番茄炒蛋",
        "ingredients": [{"name": "番茄"}, {"name": "鸡蛋"}, {"name": "盐"}],
        "steps": ["番茄切块。", "鸡蛋打散。", "热锅下油，先炒蛋再下番茄。"],
    }
    chunks = split_document(
        text="番茄炒蛋 番茄 鸡蛋 盐 番茄切块。 鸡蛋打散。",
        title="番茄炒蛋",
        document_type="recipe",
        content=content,
        count_tokens=count,
        max_tokens=60,
    )
    assert chunks
    assert all(chunk.startswith("番茄炒蛋\n") for chunk in chunks)
    joined = "\n".join(chunks)
    assert "食材：番茄、鸡蛋、盐" in joined
    assert joined.index("步骤1") < joined.index("步骤2") < joined.index("步骤3")
    assert all(len(chunk) <= 60 for chunk in chunks)


def test_long_single_step_is_split_without_loss():
    step = "字" * 100
    chunks = split_document(
        text="",
        title="菜",
        document_type="recipe",
        content={"steps": [step]},
        count_tokens=count,
        max_tokens=30,
        overlap_tokens=0,
    )
    assert len(chunks) > 1
    assert all(len(chunk) <= 30 for chunk in chunks)
    recovered = "".join(chunk.split("\n", 1)[1] for chunk in chunks)
    assert recovered == "步骤1：" + step


def test_skill_chunks_split_on_paragraphs_and_overlap():
    chunks = split_document(
        text="",
        title="技巧",
        document_type="cooking_skill",
        content={"markdown": "段落A。\n\n段落B。\n\n段落C。"},
        count_tokens=count,
        max_tokens=12,
        overlap_tokens=60,
    )
    assert chunks == ["技巧\n段落A。\n段落B。", "技巧\n段落B。\n段落C。"]


def test_empty_document_keeps_one_chunk():
    assert split_document(
        text="",
        title="空文档",
        document_type="recipe",
        content={},
        count_tokens=count,
    ) == ["空文档"]