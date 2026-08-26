# Wild ODS fixtures

Real-world `.ods` files produced by *other* generators than the LibreOffice-on-macOS used to
develop this library — Excel, an old LibreOffice 3.5, recent LibreOffice on Linux and Windows.
They exist to confront the API with the format as it is actually written in the wild (grid
fillers spanning the full 16,384 × 1,048,576 grid, missing optional package parts, ragged row
widths…), not as our own writer produces it. Exercised by `tests/test_wild_files.py`.

All files are open data published by governments. Person names present in the originals
(document authors in `meta.xml`, "responsible statistician" cells in the UK files) have been
redacted before inclusion — that is the **only** modification; the XML is otherwise
byte-identical to the published originals, and the zip structure (member order, compression,
`mimetype` first and stored) is preserved.

| File | Generator (`meta:generator`) | Source | License |
|---|---|---|---|
| `excel16_uk_stats_2026.ods` | `MicrosoftOffice/16.0 MicrosoftExcel/CalculationVersion-29822` | [gov.uk — Organised immigration crime summary tables, year ending March 2026](https://www.gov.uk/government/statistics/immigration-system-statistics-year-ending-march-2026) | [Open Government Licence v3](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/) |
| `excel16_uk_stats_2020.ods` | `MicrosoftOffice/16.0 MicrosoftExcel/CalculationVersion-25601` | [gov.uk — Transfers to the UK under section 67 of the Immigration Act 2016, year ending June 2020](https://www.gov.uk/government/statistical-data-sets/asylum-and-resettlement-datasets) | [Open Government Licence v3](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/) |
| `libreoffice35_casinos_2015.ods` | `LibreOffice/3.5$Windows_x86` (2012-era) | [data.gouv.fr — Liste des casinos autorisés en France](https://www.data.gouv.fr/fr/datasets/liste-des-casinos-de-france/) | [Licence Ouverte](https://www.etalab.gouv.fr/licence-ouverte-open-licence/) |
| `libreoffice26_linux_streets.ods` | `LibreOffice/26.2.3.2$Linux_X86_64` | [data.gouv.fr — périmètres scolaires (liste de voies)](https://www.data.gouv.fr/) | [Licence Ouverte](https://www.etalab.gouv.fr/licence-ouverte-open-licence/) |
| `libreoffice26_windows_procurement.ods` | `LibreOffice/26.2.1.2$Windows_X86_64` (created by openpyxl, resaved) | [data.gouv.fr — données essentielles de la commande publique (DECP)](https://www.data.gouv.fr/) | [Licence Ouverte](https://www.etalab.gouv.fr/licence-ouverte-open-licence/) |

Still missing from the collection: a Google Sheets export (needs a Google account to produce
one — contributions welcome).
