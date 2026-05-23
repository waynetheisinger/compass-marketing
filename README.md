# MowDirect Marketing Workspace

A marketing planning and automation workspace for **MowDirect** — a UK-based Shopify garden machinery retailer. It combines the strategic marketing documents with the scripts and tooling that integrate the platforms used to execute that strategy (Shopify, Amazon SP-API, eBay, Mirakl/B&Q, BaseLinker, NumaSuite, and the SimilarWeb/Ahrefs research stack).

The two primary artefacts are the 2026 marketing plan (`docs/MARKETING_PLAN_2026.md`) and the budget forecast (`data/finance/marketing budget forecast 2026.ods`). Everything else exists to support, measure, or automate against them.

> Detailed conventions for working in this repo — API setup, client modules, env vars, scopes — live in [`CLAUDE.md`](CLAUDE.md).

## File Organisation

The repo is organised by one question: **who reads this file, a human or a script?**

| Path | What lives there |
|---|---|
| `docs/` | Strategy + planning markdown, reference PDFs |
| `data/finance/` | Source-of-truth workbooks, statements, billing CSVs |
| `lookups/` | Cached API snapshots used as lookup tables (`shopify_catalogue.csv`, `matches.csv`, `matches_stiga.csv`) |
| `bq/` | B&Q (Kingfisher Mirakl) workspace — `csv-output`, `csv-upsert`, `csv-errors`, `templates`, `enriched` |
| `reports/` | **Human deliverables only** — decks, workbooks, briefings, audit summaries |
| `workdir/` | **Script working-state — ignore unless debugging a pipeline.** `workdir/sku-matcher/` (matcher state + supplier-input drops), `workdir/shopify-ops/` (dated Shopify-mutation receipts), `workdir/raw-pulls/` (raw API dumps + mocks) |
| `scripts/` | All Python and shell tooling — `scripts/sku_matcher/` (matcher pipeline), `scripts/report/` (monthly-report rendering) |
| `config/` | Long-lived project config JSON |
| `compliance/` | Battery test summaries, regulatory documents |

### The convention this establishes

> Never write script working artefacts (state files, dry-run previews, mutation receipts) into `reports/`.

`reports/` is reserved for things a person opens. Anything a script writes for a later step or re-run goes to `workdir/<subdir>/`. When a new script writes a file, decide its destination by asking *who reads it*:

- **A person will open it** (deck, workbook, briefing, signed-off draft, ad-hoc audit) → `reports/`.
- **A script writes it so a later step or re-run can read it** (state, dry-run preview, mutation receipt, raw API dump) → `workdir/<subdir>/`.
- **It's a lookup table future scripts will read** (cached export, SKU↔SKU map) → `lookups/`.

### Running scripts

All scripts assume the **repo root as the working directory**. Path defaults are anchored (`workdir/sku-matcher/state.json`, `lookups/matches.csv`, etc.), so a script run from any other directory will fail. The `scripts/sku_matcher/andrew_match.sh` wrapper enforces this with `cd "${REPO_ROOT}"` at the top — model new wrappers on it.
