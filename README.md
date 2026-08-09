# Product Recall Data Sources — Access Reference (US · Canada · UK · EU)

Build sheet for self-aggregating official government recall data. Every row below is a public source; access method, format, auth, and licence are noted per source. Verified against source documentation.

## Master table

| Jurisdiction | Source | Coverage | Access & endpoint | Format | Auth | Licence |
|---|---|---|---|---|---|---|
| **US** | openFDA — Food Enforcement | FDA foods: packaged, produce, seafood, dairy, supplements | `api.fda.gov/food/enforcement.json` | JSON | None (optional free key raises limits) | US public domain |
| **US** | openFDA — Drug Enforcement | Drugs | `api.fda.gov/drug/enforcement.json` | JSON | None (optional key) | US public domain |
| **US** | openFDA — Device Enforcement | Medical devices | `api.fda.gov/device/enforcement.json` | JSON | None (optional key) | US public domain |
| **US** | openFDA — Device Recalls | Medical devices | `api.fda.gov/device/recall.json` | JSON | None (optional key) | US public domain |
| **US** | CPSC | Consumer products | `saferproducts.gov/RestWebServices/Recall?format=json` | JSON/XML | None | US public domain |
| **US** | NHTSA | Vehicles | `api.nhtsa.gov/recalls/recallsByVehicle?make=&model=&modelYear=` + bulk zip `static.nhtsa.gov/odi/ffdd/rcl/` | JSON / flat file | None | US public domain |
| **US** | USDA FSIS | Meat, poultry, processed eggs | `https://www.fsis.usda.gov/fsis/api/recall/v/1` + RSS | JSON/RSS | None | US public domain |
| **Canada** | Health Canada (RSAMS) | All — Health Canada, CFIA (food) & Transport Canada (vehicles) | `recalls-rappels.canada.ca/sites/default/files/opendata-donneesouvertes/HCRSAMOpenData.json` (+ `.csv`) + REST + RSS | JSON/CSV/RSS | None | OGL-Canada |
| **UK** | Food Standards Agency | Food | `data.food.gov.uk/food-alerts` | JSON/CSV/RDF | None | OGL v3.0 |
| **UK** | OPSS | Non-food consumer products | `gov.uk/guidance/product-recalls-and-alerts` — no API, scrape | HTML | — | OGL v3.0 |
| **UK** | MHRA | Drugs & medical devices | `gov.uk/drug-device-alerts` — filterable finder + email alerts; no dedicated API | HTML | — | OGL v3.0 |
| **EU** | EU Safety Gate (RAPEX) | Non-food consumer products, EU-wide | `ec.europa.eu/safety-gate-alerts/api` + weekly Excel/XML | JSON/XML/Excel | None | CC BY 4.0 |
| **EU** | RASFF | EU food & feed (separate system) | RASFF Window public portal only — summary info, 2020+; full system authorities-only | Web | — | No open API/licence |
| **France** | RappelConso (DGCCRF/DGAL) | Food, non-food consumer goods, animal feed, **vehicles** (excl. medicines/devices) | REST API + bulk dataset on `data.economie.gouv.fr` / `data.gouv.fr` | JSON/CSV | None | Licence Ouverte 2.0 |
| **France** | ANSM | Medicines & medical-device recalls | `ansm.sante.fr` safety pages — web/scrape; no recall API | HTML | — | (adjacent open data only) |
| **Germany** | Lebensmittelwarnung (BVL) | Food, cosmetics, *Bedarfsgegenstände*, tattoo products, baby/children | RSS (all Länder + per-Land) via `lebensmittelwarnung.de/___LMW-Redaktion/RSSNewsfeed/rssnewsfeed_node.html` | RSS/XML | None | Not published — verify |
| **Germany** | BAuA / Safety Gate-DE | Non-food consumer products (toys, electronics, machinery) | BAuA "Gefährliche Produkte" DB + `rueckrufe.de` = web/scrape; **use Safety Gate API filtered to DE** | Web / JSON | None | CC BY 4.0 (via Safety Gate) |
| **Germany** | KBA Rückrufdatenbank | Vehicles, parts, accessories | Web UI with downloadable search results; no REST API | CSV/Excel | None | (not stated) |
| **Germany** | BfArM + ABDA/AMK | Medicines & medical-device recalls | BfArM site = web/scrape; ABDA AMK RSS (`abda.de/rss`) exists but full content **members-only** | HTML / RSS (gated) | Login for ABDA full content | (not open) |

---

## Resolved detail: France

France is close to Canada in breadth — one feed covers most categories cleanly, with pharma as the only gap.

**RappelConso — the main feed (clean).** Covers all general-public product recalls, food and non-food, including animal feed **and vehicles**, via a no-auth REST API with GTIN product codes, exportable on data.gouv.fr. This is broader than Germany's main feed, which does not include vehicles.

- Excludes: medicines and medical devices (those go to ANSM).
- Minor carve-out: second-hand, refurbished, and antique goods aren't subject to the RappelConso declaration obligation.

**ANSM — pharma recalls (scrape).** Medicine/device batch recalls and withdrawals are published on `ansm.sante.fr` as safety-information web pages. There is **no dedicated recall API**. The `data.ansm` open-data platform and the BDPM medicines database exist but cover *adjacent* data — pharmacovigilance, medication errors, stock shortages, marketing authorisations — **not recalls**. So French pharma recalls are scrape-only.

**Data-trust caveats (RappelConso):**
- Declarative registry — records are published by businesses themselves; DGCCRF checks after the fact, not upstream.
- No published API SLA — a risk for critical production integrations.
- Structural blind spot: products sold via non-EU / Asian marketplaces (Temu, Shein, AliExpress) are under-represented unless a targeted enforcement action forces a listing.

**France resolves to 2 sources:** RappelConso (API) + ANSM (scrape, only if pharma is in scope).

---

## Resolved detail: Germany

Germany is the most fragmented single country in this set — four categories split across four authorities, each with a different access method. It is the anti-Canada.

**1. Food / cosmetics / consumer articles — Lebensmittelwarnung (BVL). Clean RSS.**
- Feeds: one for all Länder plus one per Bundesland (16), at the RSS index page above. Categories: food, cosmetics, *Bedarfsgegenstände* (food/body-contact articles). Free, no personal data, continuous updates.
- Grab the exact per-Land feed URLs from the RSS page — the site was rebuilt on a new CMS, so old hardcoded paths may be dead.
- **Retention caveat:** records are deleted after the product's best-before/use-by date plus a safety margin; items without a durability date are typically kept ~1 year, then removed. No historical archive — you must poll and persist yourself.
- Licence: not formally published — treat reuse terms as unconfirmed.

**2. Non-food consumer products — BAuA / Safety Gate. Scrape, but redundant.**
- BAuA's "Gefährliche Produkte in Deutschland" database (consumer platform `rueckrufe.de`) publishes recalls, warnings, and prohibition orders under the Product Safety Act — web/scrape, no API or feed found.
- It substantially re-publishes a German-language extract of the weekly Safety Gate/RAPEX notifications, so it overlaps data you'd already pull cleanly from the Safety Gate API.
- **Recommended route:** Safety Gate API filtered to Germany. Use BAuA only as a scrape supplement for domestic ProdSG prohibition orders not surfaced in Safety Gate.

**3. Vehicles — KBA Rückrufdatenbank (RRDB). Structured export, no API.**
- Relaunched March 2025; search results downloadable in machine-readable formats (CSV/Excel). No REST API.
- New recalls published immediately after KBA determines owner addresses.
- **Completeness caveat:** contains only recall actions carried out using owner addresses from the central vehicle register (ZFZR), logged since 1 May 2004 — not all manufacturer actions.

**4. Medicines / medical devices — BfArM + ABDA/AMK. Scrape or gated.**
- BfArM publishes medicine recalls / batch recalls on its own website — public but web/scrape. It also runs the DMIDS device databases (mix of public and fee-based, mostly administrative data).
- The ABDA/AMK "AMK-Nachrichten" RSS feed (`abda.de/rss`) delivers recalls, batch recalls, and batch checks daily — **but the full recall content sits in the members' area behind a pharmacist login** (credentials printed in the Pharmazeutische Zeitung), and AMK notices are restricted to certain professional groups. So without pharmacy membership you get headlines, not full structured detail.
- Net: German pharma is **not** easier than French pharma — public route is BfArM scrape; the structured ABDA feed is gated.

**Germany resolves to 4 sources:** Lebensmittelwarnung (RSS) + Safety Gate-DE (API; skip BAuA) + KBA (CSV/Excel export) + BfArM scrape (ABDA feed gated). Only one of the four gives a clean, free, open feed.

---

## Structural notes

**Pharma is the perennial hard category — everywhere.** Consumer goods, food, and vehicles increasingly have clean feeds (openFDA, RappelConso, NHTSA, CPSC, Safety Gate, KBA export). Medicines and medical devices are scrape-or-restricted almost universally: UK (MHRA scrape), France (ANSM scrape), Germany (BfArM scrape / ABDA gated), EU (RASFF restricted). If pharma is out of scope, the whole job gets dramatically easier across every country.

**Non-API sources in this set (web/scrape or gated):** UK OPSS, UK MHRA, EU RASFF, France ANSM, Germany BAuA, Germany KBA (export-only, no API), Germany BfArM. Everything else is a real feed with no auth.

**Aggregation effort is driven by intra-country fragmentation, not cross-border stitching.**
- Government already aggregates (mirror the file, little to add): **Canada** (all categories, all agencies, one licensed dump). **France** largely so (one broad API + pharma gap).
- Internally fragmented (you consolidate multiple agencies even for one country): **US** (openFDA + FSIS + CPSC + NHTSA), **UK** (FSA + OPSS + MHRA), **Germany** (BVL + Safety Gate/BAuA + KBA + BfArM).

**Full food coverage takes two sources per jurisdiction** (except Canada): US = openFDA food + FSIS; EU has no usable EU-wide food feed (RASFF isn't practically integratable), so EU food is national feed by national feed.

**Persist everything on ingest.** Germany actively deletes expired records; RASFF's public window only reaches back to 2020; others prune too. Your archive will quickly become more complete than several upstream sources themselves.
