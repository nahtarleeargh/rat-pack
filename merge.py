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
    """311 complaint data per restaurant (T2 + T2b, SPEC section 9).

    Complaints carry no camis, so everything joins by bbl (96-97% coverage).
    Food complaints (Food Establishment + Food Poisoning): a bbl with exactly
    one restaurant -> that camis gets "n"; a multi-restaurant bbl -> every
    camis there gets building-level "bldg". Each entry also carries "d", the
    what-was-reported breakdown as [descriptor_id, count] pairs (ids index
    into the legend this function returns; Food Poisoning descriptors are
    victim counts, so those rows are labeled just "Food poisoning").
    Rodent complaints are property-level by nature (mostly apartments above /
    sidewalks out front), so every camis on the bbl gets the same "rod" count
    and the site must always phrase it building-level.
    Complaints without a bbl (~3-4%) are dropped rather than fuzzily matched.
    """
    since = (today - datetime.timedelta(days=365)).isoformat()
    when = f" and created_date >= '{since}T00:00:00' and bbl is not null"
    rows = soda(API_311, {
        "$select": "bbl,complaint_type,descriptor,count(*) as n",
        "$group": "bbl,complaint_type,descriptor",
        "$where": "complaint_type in('Food Establishment','Food Poisoning')" + when,
        "$limit": "50000",
    })
    per_bbl = {}    # bbl -> {label: count}
    label_totals = {}
    for row in rows:
        n = int(row["n"])
        label = (row.get("descriptor", "") or "(no descriptor)") \
            if row.get("complaint_type") == "Food Establishment" else "Food poisoning"
        d = per_bbl.setdefault(row["bbl"], {})
        d[label] = d.get(label, 0) + n
        label_totals[label] = label_totals.get(label, 0) + n

    rod_rows = soda(API_311, {
        "$select": "bbl,count(*) as n",
        "$group": "bbl",
        "$where": "complaint_type='Rodent'" + when,
        "$limit": "50000",
    })
    rod_bbl = {r["bbl"]: int(r["n"]) for r in rod_rows}

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

    # legend: descriptor strings stored once, ordered by citywide volume so
    # ids are small and roughly stable day to day
    legend = sorted(label_totals, key=label_totals.get, reverse=True)
    idx = {label: i for i, label in enumerate(legend)}

    c311 = {}
    matched = 0
    for b, camis_list in bbl_camis.items():
        food = per_bbl.get(b)
        rod = rod_bbl.get(b, 0)
        if not food and not rod:
            continue
        entry = {}
        if food:
            total = sum(food.values())
            matched += total
            entry["n" if len(camis_list) == 1 else "bldg"] = total
            entry["d"] = sorted(([idx[label], k] for label, k in food.items()),
                                key=lambda p: -p[1])
        if rod:
            entry["rod"] = rod
        for c in camis_list:
            c311[c] = entry
    total = sum(sum(d.values()) for d in per_bbl.values())
    rod_matched = sum(1 for b in bbl_camis if b in rod_bbl)
    print(f"c311: {total} food complaints w/ bbl ({matched} matched), "
          f"{sum(rod_bbl.values())} rodent complaints w/ bbl ({rod_matched} restaurant "
          f"buildings), {len(c311)} camis touched, {len(legend)} descriptors")
    return c311, legend


def build():
    today = datetime.datetime.now(datetime.timezone.utc).date()
    geo = {}      # camis -> [lat, lng]  (5-dp floats)
    c311, c311_legend = build_c311(today)  # camis -> {"n"/"bldg": int, "d": [[id, count]], "rod": int}
    lineage = {}  # camis -> {"prev": [...], "nearby": [...]}

    return {
        "meta": {
            "generated": today.isoformat(),
            "c311_descriptors": c311_legend,  # "d" ids index into this
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
