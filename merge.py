#!/usr/bin/env python3
"""merge.py - builds enrichment.json for the NYC ratings map (SPEC.md section 9).

Runs daily via GitHub Actions (see .github/workflows/merge.yml, added at T3).
Each v1.5 task fills in one section:
  T2: c311    - 311 food-safety complaint counts per restaurant / building
  T4: geo     - geocoded coordinates for restaurants missing them
  T5: lineage - predecessor / co-located establishment links
  T6: geo     - coordinates snapped to building-footprint centroids

The site treats enrichment.json as optional: absent, invalid, or older than
60 days means every enriched feature silently disappears (exact v1.2 behavior).
Keys the site does not recognize are ignored ("closed" stays reserved for the
backlogged possibly-closed flag). Output stays additive-only and camis-keyed.

Budget (measured at T6): < 2 MB raw / < 500 KB gzipped. Coords rounded to 5 dp.
"""

import datetime
import json

OUT_PATH = "enrichment.json"


def build():
    geo = {}      # camis -> [lat, lng]  (5-dp floats)
    c311 = {}     # camis -> {"n": int, "bldg": int, "top": str}
    lineage = {}  # camis -> {"prev": [...], "nearby": [...]}

    return {
        "meta": {
            "generated": datetime.datetime.now(datetime.timezone.utc).date().isoformat(),
            "sources": {
                "geo": len(geo),
                "c311": len(c311),
                "lineage": len(lineage),
            },
        },
        "geo": geo,
        "c311": c311,
        "lineage": lineage,
    }


def main():
    data = build()
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"), ensure_ascii=False)
        f.write("\n")
    print(f"wrote {OUT_PATH} (generated {data['meta']['generated']}, "
          f"sources {data['meta']['sources']})")


if __name__ == "__main__":
    main()
