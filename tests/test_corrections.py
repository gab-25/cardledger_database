from datetime import date

import pytest

from cardledger_database.corrections import Corrections, apply, load
from cardledger_database.model import CardSet, Row, SourceData


def row(id, set_code="s", number="1", variant=""):
    return Row(id, "card", "Card", set_code, number, variant, "C", ("en",), ("nonfoil",), "")


def data_with(id):
    return SourceData([row(id)], [CardSet("s", "S", None)])


def test_set_pin_renames_code_and_rebuilds_ids():
    data = SourceData([row("x:new:1", set_code="new", number="1")], [CardSet("new", "N", None)])
    out, _ = apply("x", Corrections(set=({"code": "old", "source_code": "new"},)), data)
    assert [r.id for r in out.rows] == ["x:old:1"] and [s.code for s in out.sets] == ["old"]


def test_card_fix_keeps_id():
    out, _ = apply(
        "x",
        Corrections(card=({"id": "x:s:1", "number": "1a", "rarity": "R", "image_source": "http://i"},)),
        data_with("x:s:1"),
    )
    r = out.rows[0]
    assert (r.id, r.number, r.rarity, r.image_source) == ("x:s:1", "1a", "R", "http://i")


def test_drop_removes_row():
    out, _ = apply("x", Corrections(card=({"id": "x:s:1", "drop": True},)), data_with("x:s:1"))
    assert out.rows == []


def test_set_name_and_date_fix():
    out, warnings = apply(
        "x", Corrections(set=({"code": "s", "name": "New", "released_at": date(2020, 1, 2)},)), data_with("x:s:1")
    )
    assert out.sets == [CardSet("s", "New", date(2020, 1, 2))] and warnings == []
    assert out.rows[0].id == "x:s:1"


def test_stale_card_and_set_warn():
    _, warnings = apply(
        "x", Corrections(card=({"id": "x:s:9"},), set=({"code": "zz", "name": "Z"},)), data_with("x:s:1")
    )
    assert len(warnings) == 2


def test_unknown_key_fails(tmp_path):
    (tmp_path / "x.toml").write_text('[[card]]\nid = "x:s:1"\nimage_url = "u"\n')
    with pytest.raises(ValueError, match="unknown key 'image_url'"):
        load("x", tmp_path)


def test_wrong_type_and_unknown_section_fail(tmp_path):
    (tmp_path / "x.toml").write_text('[[card]]\nid = "x:s:1"\ndrop = "yes"\n')
    with pytest.raises(ValueError, match="'drop'.*bool"):
        load("x", tmp_path)
    (tmp_path / "x.toml").write_text('[[product]]\nkey = "k"\n')
    with pytest.raises(ValueError, match="unknown section 'product'"):
        load("x", tmp_path)


def test_load_reads_card_and_set(tmp_path):
    (tmp_path / "x.toml").write_text(
        '[[card]]\nid = "x:s:1"\nrarity = "R"\n[[set]]\ncode = "s"\nreleased_at = 2020-01-02\n'
    )
    c = load("x", tmp_path)
    assert c.card == ({"id": "x:s:1", "rarity": "R"},) and c.set == ({"code": "s", "released_at": date(2020, 1, 2)},)


def test_missing_file_means_nothing(tmp_path):
    assert load("x", tmp_path) == Corrections((), ())


@pytest.mark.parametrize("value", ["2020-01-02T03:04:05", "2020-01-02T03:04:05Z", '"2020-01-02"'])
def test_released_at_must_be_a_date(tmp_path, value):
    (tmp_path / "x.toml").write_text(f'[[set]]\ncode = "s"\nreleased_at = {value}\n')
    with pytest.raises(ValueError, match=r"x\.toml: 'released_at' in \[\[set\]\] must be"):
        load("x", tmp_path)
