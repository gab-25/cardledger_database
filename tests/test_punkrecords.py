import json
import logging

import httpx
import pytest

from cardledger_database.model import CardSet, SourceError
from cardledger_database.sources import punkrecords

EN_PACKS = {
    "569101": {"id": "569101", "raw_title": "BOOSTER PACK -ROMANCE DAWN- [OP-01]",
               "title_parts": {"label": "OP-01", "prefix": "BOOSTER PACK", "title": "ROMANCE DAWN"}},
    "569202": {"id": "569202", "raw_title": "EXTRA BOOSTER -Anime 25th Collection- [EB-02]",
               "title_parts": {"label": "EB-02", "prefix": "EXTRA BOOSTER", "title": "Anime 25th Collection"}},
}
JP_PACKS = {
    "550101": {"id": "550101", "raw_title": "ブースターパック ROMANCE DAWN【OP-01】",
               "title_parts": {"label": "OP-01", "prefix": "ブースターパック", "title": "ROMANCE DAWN"}},
    "550901": {"id": "550901", "raw_title": "プロモーションカード",
               "title_parts": {"label": None, "prefix": None, "title": "プロモーションカード"}},
}
# What the real Japanese packs.json looks like: no label, the code in brackets inside the title.
JP_UNLABELLED = {"550102": {"id": "550102", "raw_title": "ブースターパック 頂上決戦【OP-02】",
                            "title_parts": {"label": None, "prefix": None, "title": "ブースターパック 頂上決戦【OP-02】"}}}


def card(key, pack, rarity="Common", host="en"):
    return {"card_id": key.split("_")[0], "pack_id": pack, "rarity": rarity, "name": f"Name {key}",
            "img_url": f"https://img/{key}.png"}


EN_CARDS = {
    "OP01-001": card("OP01-001", "569101", "Leader"),
    "OP01-001_p1": card("OP01-001_p1", "569101", "Leader"),
    "OP01-002": card("OP01-002", "569101", "Mystery"),
    "OP01-001_p2": card("OP01-001_p2", "569202", "SuperRare"),  # a parallel reprinted in another pack
    "EB02-001": card("EB02-001", "569202"),
}
JP_CARDS = {
    "OP01-001": card("OP01-001", "550101", "Leader"),
    "OP01-001_p1": card("OP01-001_p1", "550101", "Leader"),
    "P-001": card("P-001", "550901", "Promo"),
}


def client(packs=None, jp_packs=None, en_cards=None, jp_cards=None):
    data = {
        punkrecords.packs_url("english"): EN_PACKS if packs is None else packs,
        punkrecords.packs_url("japanese"): JP_PACKS if jp_packs is None else jp_packs,
        punkrecords.cards_url("english"): EN_CARDS if en_cards is None else en_cards,
        punkrecords.cards_url("japanese"): JP_CARDS if jp_cards is None else jp_cards,
    }

    def handler(request):
        if str(request.url) in data:
            return httpx.Response(200, json=data[str(request.url)])
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def by_id(data):
    return {r.id: r for r in data.rows}


def test_set_code():
    assert punkrecords.set_code("569105", "OP-05") == "op05"
    assert punkrecords.set_code("x", "OP14-EB04") == "op14-eb04"
    assert punkrecords.set_code("569901", None) == "pk569901"


def test_base_and_parallel_rows(tmp_path):
    rows = by_id(punkrecords.fetch(client(), tmp_path))
    base = rows["onepiece:op01:OP01-001"]
    assert (base.variant, base.number, base.set_code, base.kind) == ("", "OP01-001", "op01", "card")
    assert (base.rarity, base.languages, base.finishes, base.category) == ("L", ("en",), (), "")
    assert (base.name, base.source_key) == ("Name OP01-001", "english:OP01-001")
    p1 = rows["onepiece:op01:OP01-001:p1"]
    assert p1.variant == "p1" and p1.image_source == "https://img/OP01-001_p1.png"
    assert rows["onepiece:op01:OP01-002"].rarity == "Mystery"  # unknown rarity passes through
    assert rows["onepiece:eb02:OP01-001:p2"].rarity == "SR"  # same number, other set: its own row


def test_japanese_rows_are_in_their_own_sets(tmp_path):
    data = punkrecords.fetch(client(), tmp_path)
    jp = by_id(data)["onepiece:op01-jp:OP01-001:p1"]
    assert jp.languages == ("ja",) and jp.source_key == "japanese:OP01-001_p1"
    assert by_id(data)["onepiece:op01:OP01-001:p1"].languages == ("en",)
    assert CardSet("op01-jp", "ROMANCE DAWN", None, "ja") in data.sets
    assert CardSet("op01", "ROMANCE DAWN", None, "") in data.sets
    assert "onepiece:pk550901-jp:P-001" in by_id(data)


def test_japanese_code_read_from_the_title_when_there_is_no_label(tmp_path):
    data = punkrecords.fetch(client(jp_packs=JP_UNLABELLED, jp_cards={"OP02-001": card("OP02-001", "550102")}),
                             tmp_path)
    assert "onepiece:op02-jp:OP02-001" in by_id(data)
    assert CardSet("op02-jp", "ブースターパック 頂上決戦", None, "ja") in data.sets


def test_empty_packs_uses_the_cached_copy(tmp_path, caplog):
    first = punkrecords.fetch(client(), tmp_path)
    assert json.loads((tmp_path / "punkrecords" / "english-packs.json").read_text()) == EN_PACKS
    with caplog.at_level(logging.WARNING):
        second = punkrecords.fetch(client(packs={}, jp_packs={}), tmp_path)
    assert sorted(second.sets, key=lambda s: s.code) == sorted(first.sets, key=lambda s: s.code)
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2  # one per region
    assert json.loads((tmp_path / "punkrecords" / "english-packs.json").read_text()) == EN_PACKS


def test_empty_packs_without_cache_names_sets_by_code(tmp_path):
    data = punkrecords.fetch(client(packs={}, jp_packs={}), tmp_path)
    names = {s.code: s.name for s in data.sets}
    assert names["pk569101"] == "PK569101" and names["pk550901-jp"] == "PK550901-JP"
    assert not (tmp_path / "punkrecords" / "english-packs.json").exists()


def test_http_error_and_bad_cards(tmp_path):
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(SourceError, match="punk-records"):
        punkrecords.fetch(bad, tmp_path)
    with pytest.raises(SourceError, match="punk-records"):
        punkrecords.fetch(client(en_cards=[1]), tmp_path)
