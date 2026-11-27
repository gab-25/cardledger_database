import pytest

from cardledger_database import sealed


def test_sealed_rows(tmp_path):
    (tmp_path / "x.toml").write_text(
        '[[product]]\nkey = "s-box"\nname = "S Box"\nset_code = "s"\n'
        'category = "booster_box"\nimage_source = "http://box"\n'
    )
    (r,) = sealed.load("x", tmp_path)
    assert (r.id, r.kind, r.category, r.number, r.variant, r.image_source) == (
        "x:sealed:s-box",
        "sealed",
        "booster_box",
        "",
        "",
        "http://box",
    )


def test_sealed_bad_category_fails(tmp_path):
    (tmp_path / "x.toml").write_text('[[product]]\nkey = "k"\nname = "N"\nset_code = "s"\ncategory = "crate"\n')
    with pytest.raises(ValueError, match="crate"):
        sealed.load("x", tmp_path)


def test_sealed_missing_field_and_unknown_key_fail(tmp_path):
    (tmp_path / "x.toml").write_text('[[product]]\nkey = "k"\nname = "N"\nset_code = "s"\n')
    with pytest.raises(ValueError, match="without category"):
        sealed.load("x", tmp_path)
    (tmp_path / "x.toml").write_text('[[product]]\nkey = "k"\nprice = "1"\n')
    with pytest.raises(ValueError, match="unknown key 'price'"):
        sealed.load("x", tmp_path)


def test_missing_file_means_nothing(tmp_path):
    assert sealed.load("x", tmp_path) == []
