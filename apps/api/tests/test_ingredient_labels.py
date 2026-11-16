from app.services.ingredient_labels import (
    clean_corpus_names,
    clean_ingredient_names,
    clean_purchase_names,
    strip_parenthetical_notes,
)


def test_clean_ingredient_names_drops_merged_descriptors():
    assert clean_ingredient_names("荔浦芋头、实惠新鲜）") == ["荔浦芋头"]
    assert clean_ingredient_names("荔浦芋头、实惠新鲜") == ["荔浦芋头"]
    assert clean_ingredient_names("荔浦芋头（实惠新鲜）") == ["荔浦芋头"]
    assert clean_ingredient_names("荔浦芋头（实惠新鲜") == ["荔浦芋头"]


def test_clean_ingredient_names_strips_quantities_and_descriptors():
    assert clean_ingredient_names("芋头（去皮）500克") == ["芋头"]
    assert clean_ingredient_names("五花肉 约150克左右") == ["五花肉"]
    assert clean_ingredient_names("新鲜芋头") == ["芋头"]
    assert clean_ingredient_names("冷冻虾仁") == ["虾仁"]


def test_clean_ingredient_names_rejects_non_food_and_empty():
    assert clean_ingredient_names("盆") == []
    assert clean_ingredient_names("实惠新鲜）") == []
    assert clean_ingredient_names("适量") == []


def test_clean_ingredient_names_drops_unmatched_parenthetical_note_fragments():
    combined = "大白菜、冬季黄心白菜最佳）、熟白芝麻、用于点缀）"
    assert clean_ingredient_names(combined) == ["大白菜", "熟白芝麻"]
    assert clean_ingredient_names("冬季黄心白菜最佳）") == []
    assert clean_ingredient_names("用于点缀）") == []
    assert clean_ingredient_names("大白菜（冬季黄心白菜最佳）、熟白芝麻（用于点缀）") == [
        "大白菜",
        "熟白芝麻",
    ]
    assert clean_ingredient_names("冬季黄心白菜") == ["黄心白菜"]


def test_clean_ingredient_names_splits_one_field_with_multiple_foods():
    assert clean_ingredient_names("盐、糖") == ["盐", "糖"]


def test_clean_purchase_names_keeps_kitchenware_but_drops_notes():
    """A 盆 is a legitimate purchase, so only placeholders and notes are rejected."""
    assert clean_purchase_names("盆") == ["盆"]
    assert clean_purchase_names("保鲜膜") == ["保鲜膜"]
    assert clean_purchase_names("荔浦芋头、实惠新鲜）") == ["荔浦芋头"]
    assert clean_purchase_names("大白菜（冬季黄心白菜最佳）") == ["大白菜"]
    assert clean_purchase_names("熟白芝麻、用于点缀）") == ["熟白芝麻"]
    assert clean_purchase_names("适量") == []
    assert clean_ingredient_names("盆") == []


def test_clean_corpus_names_keeps_over_long_label_and_drops_noise():
    """These names also feed the restriction check, so a long label must not vanish."""
    long_label = "阿根廷红虾去壳去虾线并吸干表面水分后称重备用"
    assert len(long_label) > 20
    assert clean_corpus_names(long_label) == [long_label]
    assert clean_ingredient_names(long_label) == []
    assert clean_corpus_names("花生酱（含花生，可能致敏）") == ["花生酱"]
    assert clean_corpus_names("大白菜（冬季黄心白菜最佳）") == ["大白菜"]
    assert clean_corpus_names("盆") == []
    assert clean_corpus_names("适量") == []
    assert clean_corpus_names("冬季黄心白菜最佳）") == []
    assert clean_corpus_names("") == []


def test_strip_parenthetical_notes_keeps_the_rest_of_a_summary():
    assert strip_parenthetical_notes("荔浦芋头（实惠新鲜） 白砂糖") == "荔浦芋头 白砂糖"