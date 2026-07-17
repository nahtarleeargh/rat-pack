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
import urllib.parse
import urllib.request

OUT_PATH = "enrichment.json"
API_311 = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
API_INSP = "https://data.cityofnewyork.us/resource/43nn-pn8j.json"


def soda(url, params):
    """One Socrata GET, JSON-decoded."""
    with urllib.request.urlopen(url + "?" + urllib.parse.urlencode(params), timeout=120) as r:
        return json.load(r)


def build_c311(today):
    """311 food-safety complaint counts per restaurant (T2, SPEC section 9).

    Complaints carry no camis, so the join is by bbl (97% of food complaints
    have one). A bbl with exactly one restaurant -> that camis gets "n";
    a multi-restaurant bbl -> every camis there gets building-level "bldg".
    Complaints without a bbl (~3%) are dropped rather than fuzzily matched.
    """
    since = (today - datetime.timedelta(days=365)).isoformat()
    rows = soda(API_311, {
        # grouped by (bbl, descriptor): totals plus a majority descriptor per
        # bbl, without max()'s one-row-outlier trap (BUILD-LOG landmine 9)
        "$select": "bbl,complaint_type,descriptor,count(*) as n",
        "$group": "bbl,complaint_type,descriptor",
        "$where": "complaint_type in('Food Establishment','Food Poisoning')"
                  f" and created_date >= '{since}T00:00:00' and bbl is not null",
        "$limit": "50000",
    })
    per_bbl = {}
    for row in rows:
        b = row["bbl"]
        n = int(row["n"])
        d = per_bbl.setdefault(b, {"total": 0, "top": "", "top_n": 0})
        d["total"] += n
        if n > d["top_n"]:
            # Food Poisoning descriptors are victim counts ("1 or 2") — useless
            # as a label; the complaint type itself is the story there
            label = (row.get("descriptor", "") if row.get("complaint_type") == "Food Establishment"
                     else "Food poisoning")
            d["top"], d["top_n"] = label, n

    rest = soda(API_INSP, {
        "$select": "camis,max(bbl) as bbl",
        "$group": "camis",
        "$limit": "50000",
    })
    bbl_camis = {}
    for r in rest:
        b = r.get("bbl")
        # skip missing and placeholder bbls (boro + block 00000 + lot 0000);
        # a placeholder shared by thousands would fake one giant "building"
        if not b or b[1:] == "000000000":
            continue
        bbl_camis.setdefault(b, []).append(r["camis"])

    c311 = {}
    matched = 0
    for b, d in per_bbl.items():
        camis_list = bbl_camis.get(b)
        if not camis_list:
            continue
        matched += d["total"]
        key = "n" if len(camis_list) == 1 else "bldg"
        for c in camis_list:
            entry = {key: d["total"]}
            if d["top"]:
                entry["top"] = d["top"]
            c311[c] = entry
    total = sum(d["total"] for d in per_bbl.values())
    print(f"c311: {total} complaints w/ bbl, {matched} matched to a restaurant bbl, "
          f"{len(c311)} camis touched")
    return c311


def build():
    today = datetime.datetime.now(datetime.timezone.utc).date()
    geo = {}      # camis -> [lat, lng]  (5-dp floats)
    c311 = build_c311(today)  # camis -> {"n" or "bldg": int, "top": str}
    lineage = {}  # camis -> {"prev": [...], "nearby": [...]}

    return {
        "meta": {
            "generated": today.isoformat(),
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
