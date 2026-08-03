Let me confirm the remaining unverified rows — FSIS, Lebensmittelwarnung's current feed and licence, MHRA, and a check on openFDA.FSIS confirmed. Now MHRA and the Germany licence/feed details.MHRA confirmed. Now the Germany licence and current feed details.Every row is now verified. Here's the final table with the two previously-unconfirmed cells resolved (FSIS endpoint and Germany feed), plus concrete endpoints filled in throughout.

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
| **France** | RappelConso (DGCCRF/DGAL) | All categories incl. food | REST API + bulk dataset on `data.economie.gouv.fr` / `data.gouv.fr` | JSON/CSV | None | Licence Ouverte 2.0 |
| **Germany** | Lebensmittelwarnung (BVL) | Food, cosmetics, consumer articles, tattoo products, baby/children | RSS feeds (filterable by Bundesland or category) via `lebensmittelwarnung.de` RSS page; unofficial community API `lebensmittelwarnung.api.bund.dev` | RSS/XML | None | Not formally published |

What the confirmation resolved:

FSIS is confirmed and live. The Recall API delivers content in JSON at `https://www.fsis.usda.gov/fsis/api/recall/v/1`, filterable by field, with records carrying `field_recall_classification` (e.g. Class I), `field_recall_date`, `field_recall_number`, and `field_recall_reason`. It was FSIS's first public API, launched September 2023, and recall data also remains available via RSS. So US food = openFDA `/food/enforcement.json` + this — both confirmed, no key.

MHRA is web-only, as suspected. Alerts publish to the `gov.uk/drug-device-alerts` page as a filterable finder with email subscription for drugs and medical device alerts and recalls. No dedicated recall API — you'd scrape the finder or consume it through the general GOV.UK content/search API. One naming quirk worth knowing: MHRA renamed "Drug Alerts" to "Medicines recall/notification," and all Class 1 medicines recalls are also issued as National Patient Safety Alerts.

Germany's feed is confirmed but with two real caveats. The RSS is official and filterable by individual Bundesländer or by category (food, cosmetics, consumer articles). But: (1) I could not find a formally published open-data licence — it's official government warning content with no explicit CC/DL-DE licence stated, so treat reuse terms as unconfirmed. (2) Critically for a pipeline — entries are deleted after the product's best-before/use-by date plus a safety margin, and warnings for products without a durability date are typically published for about a year, then removed. So there's no historical archive to backfill from; you must poll continuously and persist records yourself or you'll lose them.

Two structural notes that survive confirmation:

The only genuinely non-API sources in this set are UK OPSS, UK MHRA, and EU RASFF — all web/scrape. Everything else is a real feed with no auth.

For a durable dataset, plan to store everything on ingest regardless of source. Germany actively deletes; RASFF's public window only reaches back to 2020; others prune too. Your aggregator's archive will quickly become more complete than several of the upstream sources themselves.
