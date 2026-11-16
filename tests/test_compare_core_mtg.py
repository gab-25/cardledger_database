import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "compare_core_mtg", Path(__file__).parent.parent / "scripts" / "compare_core_mtg.py"
)
compare_core_mtg = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compare_core_mtg)


def generated(id_product, **over):
    row = {"type": "product", "id_product": id_product, "name": "Card", "expansion_code": "dmu", "number": "1",
           "rarity": "common", "image_url": "s", "image_large_url": "n", "category_id": 1, "category_name": "Magic Single"}
    return {**row, **over}


def core(cm_product_id, **over):
    row = {"cm_product_id": str(cm_product_id), "name": "Card", "expansion_code": "dmu", "collector_number": "1",
           "rarity": "common", "image_url": "s", "image_large_url": "n", "category_cm_id": "1"}
    return {**row, **over}


def test_compare_counts_identical_differing_and_missing():
    gen = [generated(1), generated(2, rarity="rare"), generated(3, name="X", number="9"), generated(4)]
    ref = [core(1), core(2), core(3), core(5)]
    result = compare_core_mtg.compare(gen, ref)
    assert result.identical == 1
    assert result.differing == {"rarity": 1, "name": 1, "number": 1}
    assert result.only_generated == [4]
    assert result.only_core == [5]


def test_compare_ignores_non_product_lines_and_types():
    gen = [{"type": "expansion", "code": "dmu"}, generated(1, category_id=1)]
    result = compare_core_mtg.compare(gen, [core(1, category_cm_id="1")])
    assert result.identical == 1 and not result.differing
