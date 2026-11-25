from datetime import date

import pytest

from cardledger_database.corrections import Corrections, apply, load
from cardledger_database.model import CmProduct, Enrichment, SourceCard, SourceSet


def prod(id_product, id_expansion=10):
    return CmProduct(id_product, "Card", 1, "Single", id_expansion, None)


def write(tmp_path, text, slug="onepiece", folder="corrections"):
    (tmp_path / folder).mkdir(exist_ok=True)
    (tmp_path / folder / f"{slug}.toml").write_text(text)
    return load(slug, tmp_path / "corrections", tmp_path / "expansions")


def base():
    return Enrichment(
        {1: SourceCard("op01", "1", "C", "small", "large")},
        {"op01": SourceSet("op01", "Romance Dawn", date(2022, 12, 2))},
        {10: "op01"},
    )


def test_missing_file_is_empty(tmp_path):
    assert load("mtg", tmp_path / "corrections", tmp_path / "expansions") == Corrections((), ())


def test_product_fields_override_match():
    c = write_inline('[[product]]\nid_product = 1\nrarity = "SR"\nimage_url = "new"\n')
    e, warnings = apply(c, [prod(1)], base())
    assert e.cards[1] == SourceCard("op01", "1", "SR", "new", "large")
    assert warnings == []


def test_unmatched_removes_match():
    c = write_inline("[[product]]\nid_product = 1\nunmatched = true\n")
    e, _ = apply(c, [prod(1)], base())
    assert 1 not in e.cards


def test_new_card_needs_set_code():
    c = write_inline(
        '[[product]]\nid_product = 2\nset_code = "op02"\nrarity = "L"\n\n[[product]]\nid_product = 3\nrarity = "L"\n'
    )
    e, warnings = apply(c, [prod(2), prod(3)], base())
    assert e.cards[2] == SourceCard("op02", "", "L", "", "")
    assert 3 not in e.cards and len(warnings) == 1 and "3" in warnings[0]


def test_expansion_override_sets_code_name_and_date():
    c = write_inline(
        '[[expansion]]\nid_expansion = 10\ncode = "OP05"\nname = "Awakening"\nreleased_at = 2023-08-25\n',
        "expansions",
    )
    e, _ = apply(c, [prod(1)], base())
    assert e.expansions[10] == "op05"
    assert e.sets["op05"] == SourceSet("op05", "Awakening", date(2023, 8, 25))


def test_expansion_defaults_name_to_code_upper():
    c = write_inline('[[expansion]]\nid_expansion = 10\ncode = "op09"\n', "expansions")
    e, _ = apply(c, [prod(1)], base())
    assert e.sets["op09"] == SourceSet("op09", "OP09", None)


def test_correction_for_missing_product_is_skipped(tmp_path):
    write(tmp_path, '[[expansion]]\nid_expansion = 77\ncode = "x"\n', folder="expansions")
    c = write(tmp_path, '[[product]]\nid_product = 99\nrarity = "SR"\n')
    original = base()
    e, warnings = apply(c, [prod(1)], original)
    assert e == original
    assert len(warnings) == 2 and "99" in warnings[0] and "77" in warnings[1]


def test_invalid_toml_raises(tmp_path):
    with pytest.raises(ValueError, match="onepiece.toml"):
        write(tmp_path, "[[product\n")


def test_wrong_type_and_unknown_key_raise(tmp_path):
    with pytest.raises(ValueError, match="onepiece.toml"):
        write(tmp_path, '[[product]]\nid_product = "x"\n')
    with pytest.raises(ValueError, match="onepiece.toml"):
        write(tmp_path, "[[product]]\nid_product = 1\nbogus = 1\n")


def test_both_files_are_read(tmp_path):
    write(tmp_path, '[[expansion]]\nid_expansion = 10\ncode = "cmx10"\nname = "Promos"\n', folder="expansions")
    c = write(tmp_path, '[[product]]\nid_product = 1\nrarity = "SR"\n')
    assert [e["id_expansion"] for e in c.expansion] == [10]
    assert [p["id_product"] for p in c.product] == [1]


def test_expansion_section_in_corrections_raises(tmp_path):
    with pytest.raises(ValueError, match="corrections.*expansion"):
        write(tmp_path, '[[expansion]]\nid_expansion = 10\ncode = "x"\n')


def test_product_section_in_expansions_raises(tmp_path):
    with pytest.raises(ValueError, match="expansions.*product"):
        write(tmp_path, "[[product]]\nid_product = 1\n", folder="expansions")


def test_expansions_file_is_checked(tmp_path):
    with pytest.raises(ValueError, match="expansions"):
        write(tmp_path, "[[expansion]]\nid_expansion = 10\nbogus = 1\n", folder="expansions")


def write_inline(text, folder="corrections"):
    import tempfile
    from pathlib import Path

    d = Path(tempfile.mkdtemp())
    return write(d, text, folder=folder)
