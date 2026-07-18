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
import re
import time
import urllib.parse
import urllib.request

OUT_PATH = "enrichment.json"
API_311 = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
API_INSP = "https://data.cityofnewyork.us/resource/43nn-pn8j.json"
API_GEOSEARCH = "https://geosearch.planninglabs.nyc/v2/search"


def soda(url, params):
    """One Socrata GET, JSON-decoded."""
    with urllib.request.urlopen(url + "?" + urllib.parse.urlencode(params), timeout=120) as r:
        return json.load(r)


def geosearch(text):
    """Top GeoSearch v2 hit for an address string, or None if it has none.

    Retries twice on network errors, then raises: a persistent failure means
    the service is down, and crashing the run (no commit, yesterday's file
    stays live) beats committing a file whose geo section silently shrank.
    """
    qs = urllib.parse.urlencode({"text": text, "size": "1"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(API_GEOSEARCH + "?" + qs, timeout=30) as r:
                feats = json.load(r).get("features") or []
                return feats[0] if feats else None
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2)


def build_geo(today):
    """Repaired coordinates for restaurants the dataset ships without (T4).

    ~800 of ~31k camis have missing/zero lat-lng. Each gets one GeoSearch v2
    lookup ("building street, boro, NY"); a hit is accepted only when its
    confidence is >= 0.7 AND its borough or zip agrees with the dataset row —
    the agreement gate is what kills Pelias fallback matches that land in the
    wrong borough (e.g. a Central Park transverse "matched" to 65 St Brooklyn).
    Rejects and no-results simply stay coordinate-less (off the map, as today).
    Coords are rounded to 5 dp (~1 m) for the JSON budget and byte-stability.
    """
    rows = soda(API_INSP, {
        "$select": "camis,max(building) as bldg,max(street) as street,"
                   "max(boro) as boro,max(zipcode) as zip,"
                   "max(latitude) as lat,max(longitude) as lng",
        "$group": "camis",
        "$limit": "50000",
    })

    def coordless(r):
        try:
            return float(r.get("lat", "")) == 0 or float(r.get("lng", "")) == 0
        except ValueError:
            return True

    missing = sorted((r for r in rows if coordless(r)), key=lambda r: r["camis"])
    geo = {}
    stats = {"no_street": 0, "no_result": 0, "low_conf": 0, "disagree": 0}
    for r in missing:
        street = (r.get("street") or "").strip()
        if not street:
            stats["no_street"] += 1
            continue
        bldg = (r.get("bldg") or "").strip()
        boro = (r.get("boro") or "").strip()
        zip_ = (r.get("zip") or "").strip()
        text = f"{bldg} {street}".strip() + (f", {boro}" if boro not in ("", "0") else "") + ", NY"
        hit = geosearch(text)
        time.sleep(0.05)  # politeness: public API, no key, daily cron
        if hit is None:
            stats["no_result"] += 1
            continue
        props = hit.get("properties", {})
        if (props.get("confidence") or 0) < 0.7:
            stats["low_conf"] += 1
            continue
        boro_ok = boro not in ("", "0") and (props.get("borough") or "").lower() == boro.lower()
        zip_ok = zip_ != "" and (props.get("postalcode") or "") == zip_
        if not (boro_ok or zip_ok):
            stats["disagree"] += 1
            continue
        lng, lat = hit["geometry"]["coordinates"]
        geo[r["camis"]] = [round(lat, 5), round(lng, 5)]
    print(f"geo: {len(missing)} camis missing coords, {len(geo)} repaired, "
          f"rejected {stats}")
    return geo


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
    # ids are small and roughly stable day to day. Every ordering below
    # carries a deterministic tie-break: Socrata returns grouped rows in
    # arbitrary per-request order, and any order leaking into the output
    # makes the daily cron commit spurious byte-diffs (bit us at T3: 1,631
    # entries reshuffled between two runs minutes apart)
    legend = sorted(label_totals, key=lambda l: (-label_totals[l], l))
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
                                key=lambda p: (-p[1], p[0]))
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


# Address normalization for the lineage fallback key — mirrors index.html's
# normAddr so both sides agree on what "same address" means: uppercase,
# punctuation to spaces, ordinals stripped (1ST -> 1), abbreviations expanded.
ADDR_ABBREV = {
    "AVE": "AVENUE", "AV": "AVENUE", "ST": "STREET", "BLVD": "BOULEVARD",
    "RD": "ROAD", "DR": "DRIVE", "PL": "PLACE", "PKWY": "PARKWAY",
    "LN": "LANE", "CT": "COURT", "TER": "TERRACE", "SQ": "SQUARE",
    "BWAY": "BROADWAY", "BDWY": "BROADWAY",
    "E": "EAST", "W": "WEST", "N": "NORTH", "S": "SOUTH",
}


def norm_addr(s):
    out = []
    for w in re.sub(r"[^A-Z0-9 ]+", " ", str(s).upper()).split():
        m = re.fullmatch(r"(\d+)(ST|ND|RD|TH)", w)
        out.append(m.group(1) if m else ADDR_ABBREV.get(w, w))
    return " ".join(out)


def name_sim(a, b):
    """Token Jaccard similarity between two establishment names (0..1)."""
    ta = set(re.findall(r"[A-Z0-9]+", (a or "").upper()))
    tb = set(re.findall(r"[A-Z0-9]+", (b or "").upper()))
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


PREV_CAP = 3  # succession lines per camis; less-plausible ones fall to nearby


def build_lineage(today):
    """Predecessor / co-located links per restaurant (T5, SPEC section 9).

    Groups camis by building: bin where present (placeholder "million" bins
    like 3000000 rejected), else normalized building+street+boro. Within a
    group, another permit whose inspections ALL ended before this one's began
    (strict, no overlap; never-inspected sentinels have no dates and never
    qualify) is a likely predecessor -> "prev", most plausible first (name
    similarity boosts the ordering, then most recently seen; similarity is
    never required). prev is capped at PREV_CAP: in big multi-tenant buildings
    dozens of expired permits "predate" any new one, and those cross-storefront
    guesses belong in the low-confidence bucket, not stacked as claims.
    Everyone else at the location is the low-confidence "nearby" bucket, which
    the SITE derives as group minus self minus prev: each group's member list
    is stored once ("lgroups", entries point at it via "g") because per-member
    neighbor lists repeat every camis k times in a k-permit building — measured
    1.48 MB that way vs ~0.9 MB this way, against a 2 MB budget shared with
    T6. Names ship once per camis in a top-level map, same reasoning as the
    c311 descriptor legend. Nothing is dropped: every member of every group is
    reachable from every other member's panel.
    LINK, never merge: entries only reference other camis; each keeps its own
    grades, history, and pin on the site.
    """
    rows = soda(API_INSP, {
        "$select": "camis,max(dba) as name,max(bin) as bin_num,"
                   "max(building) as bldg,max(street) as street,max(boro) as boro",
        "$group": "camis",
        "$limit": "50000",
    })
    # inspection span per camis, excluding the 1900-01-01 never-inspected sentinel
    dates = soda(API_INSP, {
        "$select": "camis,min(inspection_date) as fi,max(inspection_date) as li",
        "$group": "camis",
        "$where": "inspection_date > '1900-01-02'",
        "$limit": "50000",
    })
    span = {r["camis"]: (r["fi"][:10], r["li"][:10]) for r in dates}

    names = {}
    groups = {}
    fallback = 0
    for r in rows:
        camis = r["camis"]
        names[camis] = (r.get("name") or "").strip() or "(unnamed)"
        b = (r.get("bin_num") or "").strip()
        if b and b[1:] != "000000":
            key = "b:" + b
        else:
            addr = norm_addr((r.get("bldg") or "") + " " + (r.get("street") or ""))
            boro = (r.get("boro") or "").strip().upper()
            if not addr or boro in ("", "0"):
                continue  # no usable location key at all
            key = "a:" + boro + "|" + addr
            fallback += 1
        groups.setdefault(key, []).append(camis)

    lineage = {}
    lgroups = []
    out_names = {}
    prev_pairs = capped = 0
    biggest = 0
    for key in sorted(groups):
        members = sorted(groups[key])
        if len(members) < 2:
            continue
        biggest = max(biggest, len(members))
        gi = len(lgroups)
        lgroups.append(members)
        for c in members:
            out_names[c] = names[c]
            c_first = span.get(c, (None, None))[0]
            prev = []
            if c_first:
                prev = [o for o in members
                        if o != c and span.get(o, (None, None))[1]
                        and span[o][1] < c_first]
            entry = {"g": gi}
            if prev:
                prev.sort(key=lambda o: (name_sim(names[c], names[o]) < 0.5,
                                         -int(span[o][1].replace("-", "")), o))
                if len(prev) > PREV_CAP:
                    prev = prev[:PREV_CAP]
                    capped += 1
                entry["prev"] = [[o, span[o][1][:7]] for o in prev]
                prev_pairs += len(prev)
            lineage[c] = entry
    print(f"lineage: {len(lgroups)} multi-permit locations ({fallback} camis keyed "
          f"by address fallback), {len(lineage)} camis with entries, {prev_pairs} "
          f"prev links ({capped} capped at {PREV_CAP}), biggest group {biggest}, "
          f"{len(out_names)} names")
    return lineage, lgroups, out_names


def build():
    today = datetime.datetime.now(datetime.timezone.utc).date()
    geo = build_geo(today)  # camis -> [lat, lng]  (5-dp floats)
    c311, c311_legend = build_c311(today)  # camis -> {"n"/"bldg": int, "d": [[id, count]], "rod": int}
    # camis -> {"prev": [[camis, "YYYY-MM"]], "g": index into lgroups}
    lineage, lgroups, names = build_lineage(today)

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
        "lgroups": lgroups,  # member lists stored once; entries point via "g"
        "names": names,  # dba for every camis lineage references, stored once
    }


def main():
    data = build()
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
        f.write("\n")
    print(f"wrote {OUT_PATH} (generated {data['meta']['generated']}, "
          f"sources {data['meta']['sources']})")


if __name__ == "__main__":
    main()
