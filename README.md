# Fat Rat Map 🐀

A map of every NYC restaurant's health inspection grade and history, on one page.

**Live at: https://fatratmap.com**

Tap any dot to see a restaurant's current grade, its full inspection history with
plain-English explanations of what the inspector found, how its score compares to the
city median, 311 complaints reported in the last 12 months, and what was at that
address before it.

## How to read the map

- **Green / amber / red dots** are current grades A, B and C. Grey means a grade is
  pending, hollow means the place has never been inspected. Honest states are shown,
  not hidden.
- **A red ring** means the restaurant is currently closed by the Health Department.
  A dashed amber ring means it was closed in the past and has re-opened.
- **A number in a dot** means several restaurants share that exact spot (food halls,
  terminals, big buildings). Tap it to pick one from the list.
- Dots sit on their buildings, not on street centerlines, joined through the city's
  building-footprint data.

## How it works

The whole app is one static file, `index.html`. No build step, no backend, no database,
no analytics. The browser queries NYC Open Data directly and draws ~31,000 restaurants
with a custom canvas renderer.

A small pipeline, `merge.py`, runs once a day in GitHub Actions and commits
`enrichment.json`, which the site fetches at load time. That file adds what the live
queries can't do cheaply:

- **311 complaint counts** per restaurant or building for the last 12 months, with a
  breakdown of what was reported. Complaints don't name businesses, so counts in
  multi-restaurant buildings are labeled building-level rather than pinned on one place.
- **Repaired coordinates** for ~500 restaurants the city dataset ships without usable
  ones, geocoded through NYC GeoSearch with conservative acceptance rules.
- **Address history**: what operated at this address before, matched on building ID and
  inspection timing. Links only, never merged. A new owner's clean slate stays clean.
- **Building footprint points** so every dot lands on its actual building.

Every enriched element shows the date its data was generated. If the enrichment file is
missing or more than 60 days stale, those features quietly disappear and the map still
works.

## Data sources

| What | Dataset |
|---|---|
| Restaurant inspections | [DOHMH Restaurant Inspection Results](https://data.cityofnewyork.us/Health/DOHMH-New-York-City-Restaurant-Inspection-Results/43nn-pn8j) (`43nn-pn8j`) |
| 311 complaints | [311 Service Requests](https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2010-to-Present/erm2-nwe9) (`erm2-nwe9`) |
| Building footprints | [Building Footprints](https://data.cityofnewyork.us/City-Government/Building-Footprints/5zhs-2jue) (`5zhs-2jue`) |
| Geocoding | [NYC GeoSearch](https://geosearch.planninglabs.nyc/) |

Map tiles by [CARTO](https://carto.com/) on [OpenStreetMap](https://www.openstreetmap.org/)
data, rendered with [Leaflet](https://leafletjs.com/).

Grades and inspection records are the city's own data, shown as published. Mistakes in
the source data will appear here too; this site adds context, never edits the record.

## Running it locally

```
python -m http.server 8123
```

Open http://localhost:8123. That's it. Geolocation and clipboard features want
localhost or HTTPS.

To rebuild the data file yourself: `python merge.py` (takes ~15 minutes, mostly polite
rate-limiting on ~800 geocoding calls, and needs no API keys).
