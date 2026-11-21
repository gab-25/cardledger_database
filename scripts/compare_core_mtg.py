"""Compare the generated Magic catalog with a dump of core's blueprints.

Usage: python scripts/compare_core_mtg.py <generated mtg .jsonl.gz> <core dump .csv>

Export core's dump right after a `sync_catalog` run the same day, from the core checkout:

    cd ~/projects/cardledger_core && uv run python manage.py shell -c "import csv,sys; from ledger.models import Blueprint; w=csv.writer(open('/tmp/core_blueprints.csv','w',newline='')); w.writerow(['cm_product_id','name','expansion_code','collector_number','rarity','image_url','image_large_url','category_cm_id']); [w.writerow([b.cm_product_id, b.name, b.expansion.code, b.collector_number, b.rarity, b.image_url, b.image_large_url, b.category.cm_id if b.category else '']) for b in Blueprint.objects.select_related('expansion','category').iterator()]"

Prints the identical rows, the rows differing per field and the ids found on one side only. Products new or
changed between the two downloads are expected to show up as differences.
"""

import csv
import gzip
import json
import sys
from collections import Counter
from dataclasses import dataclass, field

# generated field -> core CSV column
FIELDS = {
    "name": "name",
    "expansion_code": "expansion_code",
    "number": "collector_number",
    "rarity": "rarity",
    "image_url": "image_url",
    "image_large_url": "image_large_url",
    "category_id": "category_cm_id",
}


@dataclass
class Comparison:
    identical: int = 0
    differing: Counter = field(default_factory=Counter)
    only_generated: list[int] = field(default_factory=list)
    only_core: list[int] = field(default_factory=list)


def _text(value):
    return "" if value is None else str(value)


def compare(generated_rows, core_rows) -> Comparison:
    """Compare generated product lines (dicts) with core CSV rows (dicts), keyed by Cardmarket product id."""
    generated = {int(r["id_product"]): r for r in generated_rows if r.get("type", "product") == "product"}
    core = {int(r["cm_product_id"]): r for r in core_rows}
    result = Comparison()
    for id_product in sorted(generated.keys() & core.keys()):
        diff = [f for f, col in FIELDS.items() if _text(generated[id_product][f]) != _text(core[id_product][col])]
        if diff:
            result.differing.update(diff)
        else:
            result.identical += 1
    result.only_generated = sorted(generated.keys() - core.keys())
    result.only_core = sorted(core.keys() - generated.keys())
    return result


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    with gzip.open(argv[1], "rt", encoding="utf-8") as f:
        generated_rows = [json.loads(line) for line in f if line.strip()]
    with open(argv[2], newline="", encoding="utf-8") as f:
        core_rows = list(csv.DictReader(f))
    result = compare(generated_rows, core_rows)
    print(f"identical rows: {result.identical}")
    print(f"differing fields: {sum(result.differing.values())} field differences")
    for name, count in result.differing.most_common():
        print(f"  {name}: {count}")
    print(f"only in generated: {len(result.only_generated)} {result.only_generated[:10]}")
    print(f"only in core: {len(result.only_core)} {result.only_core[:10]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
