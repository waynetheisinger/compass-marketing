"""
Regression test for the monthly-report "real spreadsheet" formulas.

The workbook now writes derived cells as live Excel formulas instead of
pre-computed values (subtotals, grand totals, percentages, and the Summary
tab's cross-references into the detail tabs). openpyxl writes the formula
text but does not evaluate it, so this test ships a small pure-Python
evaluator for the limited formula vocabulary we emit:

    SUM(range)         contiguous single-column range
    IFERROR(expr, fb)  safe ratio fallback
    + - * /            arithmetic on cell refs and numeric literals
    'Sheet'!A1         cross-sheet single-cell reference
    A1                 same-sheet single-cell reference

It then asserts:
  1. Every formula cell in the workbook evaluates to a finite number
     (catches broken ranges / #REF drift anywhere).
  2. The headline Summary figures equal the values transforms.py produced
     (the oracle) within a penny.
  3. The Summary cross-references resolve to the detail-tab grand totals
     (proves the cross-linking actually wires up).

Run:
    PYTHONPATH=. python3.11 -m unittest tests.test_report_formulas -v
"""
from __future__ import annotations

import re
import unittest

from scripts.report import transforms as tr
from scripts.report.excel_writer import (
    build_workbook,
    SHEET_SUMMARY, SHEET_MARKETPLACE, SHEET_ADSPEND,
)


# ---------------------------------------------------------------------------
# Minimal formula evaluator (covers exactly the vocabulary excel_writer emits)
# ---------------------------------------------------------------------------

_COORD_RE = re.compile(r"^([A-Z]{1,3})(\d+)$")


def _coord_parts(coord: str) -> tuple[str, int]:
    m = _COORD_RE.match(coord)
    if not m:
        raise ValueError(f"bad coord {coord!r}")
    return m.group(1), int(m.group(2))


def eval_cell(wb, sheet: str, coord: str, memo: dict) -> float:
    key = (sheet, coord)
    if key in memo:
        return memo[key]
    memo[key] = 0.0  # cycle guard (refs form a DAG, so this is never read back)
    val = wb[sheet][coord].value
    result = eval_value(wb, sheet, val, memo)
    memo[key] = result
    return result


def eval_value(wb, sheet: str, val, memo: dict) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val)
    if not s.startswith("="):
        # Text cell ("—", a label, a note) — contributes 0 to any aggregate.
        return 0.0
    return eval_formula(wb, sheet, s[1:], memo)


def _sum_range(wb, sheet: str, rng: str, memo: dict) -> float:
    start, end = rng.split(":")
    col_s, row_s = _coord_parts(start)
    col_e, row_e = _coord_parts(end)
    assert col_s == col_e, f"non-single-column SUM range {rng!r}"
    return sum(
        eval_cell(wb, sheet, f"{col_s}{row}", memo)
        for row in range(row_s, row_e + 1)
    )


def eval_formula(wb, sheet: str, expr: str, memo: dict) -> float:
    # 1. SUM(range) -> number  (ranges contain no nested functions)
    expr = re.sub(
        r"SUM\(([^)]*)\)",
        lambda m: repr(_sum_range(wb, sheet, m.group(1), memo)),
        expr,
    )

    # 2. IFERROR(inner, fallback) -> number  (inner fully evaluated; on a
    #    zero-division it falls back). After SUM substitution there are no
    #    nested parens, so a non-greedy split on the comma is safe.
    def _iferror(m):
        inner, fallback = m.group(1), m.group(2)
        try:
            return repr(eval_formula(wb, sheet, inner, memo))
        except ZeroDivisionError:
            return repr(eval_formula(wb, sheet, fallback, memo))
    expr = re.sub(r"IFERROR\(([^,]*),\s*([^)]*)\)", _iferror, expr)

    # 3. Cross-sheet refs:  'Sheet Name'!A1
    expr = re.sub(
        r"'([^']+)'!\$?([A-Z]{1,3})\$?(\d+)",
        lambda m: repr(eval_cell(wb, m.group(1), f"{m.group(2)}{m.group(3)}", memo)),
        expr,
    )

    # 4. Same-sheet refs:  A1  (not preceded by ! or ' — those were handled above)
    expr = re.sub(
        r"(?<![A-Za-z0-9_'!])\$?([A-Z]{1,3})\$?(\d+)",
        lambda m: repr(eval_cell(wb, sheet, f"{m.group(1)}{m.group(2)}", memo)),
        expr,
    )

    return float(eval(expr, {"__builtins__": {}}, {}))


# ---------------------------------------------------------------------------
# Synthetic fixture — exercises every formula-bearing tab
# ---------------------------------------------------------------------------

def _build_fixture() -> dict:
    channels = [
        {"name": "Shopify Direct", "source": "Shopify GraphQL", "net": 45000.00,
         "fee_rows": [{"label": "Payment processing fees", "amount": 810.00}]},
        {"name": "eBay", "source": "eBay Finances API", "net": 12000.00,
         "fee_rows": [
             {"label": "Final value fees", "amount": 1320.00},
             {"label": "Regulatory operating fee", "amount": 36.00},
         ]},
        {"name": "Amazon", "source": "SP-API Settlement", "net": 20000.00,
         "fee_rows": [{"label": "Referral fees", "amount": 3000.00}]},
        {"name": "B&Q (Mirakl)", "source": "Mirakl API", "net": 6000.00,
         "fee_rows": [
             {"label": "Commission", "amount": 720.00},
             {"label": "Platform charge", "amount": 0.00},   # zero-suppressed
         ]},
        # Channel with net but NO fee rows — e.g. Amazon before settlements
        # post mid-month. Its net must still flow into the grand total.
        {"name": "ManoMano", "source": "BaseLinker", "net": 9395.32,
         "fee_rows": []},
    ]
    for ch in channels:
        ch["total_fees"] = sum(r["amount"] for r in ch["fee_rows"])
        ch["note"] = None

    ad_rows = [
        {"platform": "Google Ads", "campaign_name": "PMax — Spectrum",
         "campaign_type": "PMax", "spend": 2500.00, "impressions": 120000,
         "clicks": 4800, "conversions": 90.0, "conversions_value": 18000.00},
        {"platform": "Google Ads", "campaign_name": "Shopping — Honda",
         "campaign_type": "Shopping", "spend": 900.00, "impressions": 50000,
         "clicks": 1500, "conversions": 25.0, "conversions_value": 6000.00},
        {"platform": "eBay", "campaign_name": "Promoted Listings",
         "campaign_type": "PROMOTED_LISTINGS", "spend": 430.00,
         "impressions": None, "clicks": None,
         "conversions": None, "conversions_value": None},
        {"platform": "Amazon", "campaign_name": "SP — Mowers",
         "campaign_type": "Sponsored Products", "spend": 600.00,
         "impressions": 30000, "clicks": 900,
         "conversions": 18.0, "conversions_value": 3600.00},
        {"platform": "Amazon", "campaign_name": "SP — Trimmers",
         "campaign_type": "Sponsored Products", "spend": 300.00,
         "impressions": 15000, "clicks": 400,
         "conversions": 8.0, "conversions_value": 1500.00},
    ]

    net_by_channel = {ch["name"]: ch["net"] for ch in channels}
    all_fee_rows = [r for ch in channels for r in ch["fee_rows"]]
    auto_commission = round(sum(net_by_channel.values()) * 0.04, 2)
    summary = tr.build_summary(
        all_fee_rows, ad_rows, net_by_channel,
        wayne_commission=auto_commission,
        wayne_commission_note="4% of Net Revenue across all channels (pre-audit estimate).",
        wayne_commission_overridden=False,
    )
    summary["commission_rate"] = 0.04

    def _platform_spend(name):
        return round(sum(r["spend"] for r in ad_rows if r["platform"] == name), 2)

    ad_platform_summary = [
        {"platform": "Google Ads", "spend": _platform_spend("Google Ads"), "note": None},
        {"platform": "eBay Promoted Listings", "spend": _platform_spend("eBay"), "note": None},
        {"platform": "Amazon Sponsored Products", "spend": _platform_spend("Amazon"), "note": None},
    ]

    cancellations = {
        "Shopify Direct": {
            "total_orders": 3, "total_value": 1200.00,
            "by_reason": [
                {"label": "Customer", "orders": 2, "value": 800.00},
                {"label": "Seller (out of stock)", "orders": 1, "value": 400.00},
            ],
        },
        "Amazon": {
            "total_orders": 1, "total_value": 250.00,
            "by_reason": [{"label": "Cancelled", "orders": 1, "value": 250.00}],
        },
        "B&Q (Mirakl)": None,
    }
    cancellation_notes = {
        "Shopify Direct": None,
        "Amazon": None,
        "B&Q (Mirakl)": "NOT CONNECTED — Mirakl credentials missing",
    }

    fba_returns = {
        "customer_returns_summary": {
            "by_disposition": [
                {"disposition": "SELLABLE", "label": "Returned to sellable stock",
                 "units": 12, "lines": 9},
                {"disposition": "CUSTOMER_DAMAGED", "label": "Unsellable — customer damaged",
                 "units": 5, "lines": 4},
            ],
            "total_units": 17, "total_lines": 13,
            "sellable_units": 12, "unsellable_units": 5,
        },
        "removal_shipments_summary": {
            "by_type": [
                {"order_type": "Return", "units": 8, "lines": 3},
                {"order_type": "Disposal", "units": 4, "lines": 2},
            ],
            "total_units": 12,
        },
        "inventory_snapshot_summary": {
            "sku_count": 13, "fulfillable": 540, "unfulfillable": 22,
            "researching": 3, "inbound_working": 0, "inbound_shipped": 60,
            "inbound_receiving": 10, "available_to_pickup": 25,
        },
        "removal_fee_totals": {
            "FBA removal fees": 18.40,
            "FBA disposal fees": 6.00,
        },
    }

    return {
        "summary": summary,
        "channels": channels,
        "fba_returns": fba_returns,
        "fba_returns_note": None,
        "cancellations": cancellations,
        "cancellation_notes": cancellation_notes,
        "ad_spend_rows": ad_rows,
        "ad_spend_not_connected": [],
        "ad_spend_platform_summary": ad_platform_summary,
        "ad_spend_notes": {},
        "ebay_raw_transactions": [],
        "amazon_raw_fees": [],
        "baselinker_raw_orders": [],
        "google_ads_raw": [],
        "is_mock": False,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class FormulaWorkbookTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = _build_fixture()
        cls.anchors = {}
        cls.wb = build_workbook(cls.data, "March 2026", collect_anchors=cls.anchors)

    def _eval(self, sheet, col_letter, row):
        return eval_cell(self.wb, sheet, f"{col_letter}{row}", {})

    def test_full_calc_on_load_flag(self):
        self.assertTrue(self.wb.calculation.fullCalcOnLoad,
                        "workbook must request recalculation on open")

    def test_summary_is_first_sheet(self):
        self.assertEqual(self.wb.sheetnames[0], SHEET_SUMMARY)

    def test_every_formula_cell_evaluates(self):
        """No formula anywhere should reference a broken cell / range."""
        formula_count = 0
        for ws in self.wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    v = cell.value
                    if isinstance(v, str) and v.startswith("="):
                        formula_count += 1
                        try:
                            result = eval_cell(self.wb, ws.title, cell.coordinate, {})
                        except Exception as exc:  # noqa: BLE001
                            self.fail(
                                f"{ws.title}!{cell.coordinate} = {v!r} "
                                f"failed to evaluate: {exc}"
                            )
                        self.assertEqual(result, result,  # not NaN
                                         f"{ws.title}!{cell.coordinate} is NaN")
        # Sanity: we should actually be emitting a meaningful number of formulas.
        self.assertGreater(formula_count, 20,
                           "expected the workbook to contain many formulas")

    def test_headline_matches_oracle(self):
        s = self.data["summary"]
        a = self.anchors["summary"]
        self.assertAlmostEqual(self._eval(SHEET_SUMMARY, "B", a["net_row"]),
                               s["net"], delta=0.02)
        self.assertAlmostEqual(self._eval(SHEET_SUMMARY, "B", a["fees_row"]),
                               s["total_fees"], delta=0.02)
        self.assertAlmostEqual(self._eval(SHEET_SUMMARY, "B", a["commission_row"]),
                               s["wayne_commission"], delta=0.02)
        self.assertAlmostEqual(self._eval(SHEET_SUMMARY, "B", a["ads_row"]),
                               s["total_ads"], delta=0.02)
        self.assertAlmostEqual(self._eval(SHEET_SUMMARY, "B", a["deductions_row"]),
                               s["combined"], delta=0.02)
        self.assertAlmostEqual(self._eval(SHEET_SUMMARY, "B", a["contribution_row"]),
                               s["contribution"], delta=0.02)

    def test_cross_links_resolve_to_detail_tabs(self):
        """Summary headline cells must equal the detail-tab grand totals."""
        a = self.anchors
        summary_net = self._eval(SHEET_SUMMARY, "B", a["summary"]["net_row"])
        mp_net      = self._eval(SHEET_MARKETPLACE, "D", a["mp_grand_net_row"])
        self.assertAlmostEqual(summary_net, mp_net, delta=0.001)

        summary_fees = self._eval(SHEET_SUMMARY, "B", a["summary"]["fees_row"])
        mp_fees      = self._eval(SHEET_MARKETPLACE, "C", a["mp_grand_fees_row"])
        self.assertAlmostEqual(summary_fees, mp_fees, delta=0.001)

        summary_ads = self._eval(SHEET_SUMMARY, "B", a["summary"]["ads_row"])
        ads_grand   = self._eval(SHEET_ADSPEND, "C", a["ads_grand_row"])
        self.assertAlmostEqual(summary_ads, ads_grand, delta=0.001)

    def test_no_fee_channel_net_in_grand_total(self):
        """A channel with net but zero fee rows must still count toward net."""
        # Marketplace grand-total net must equal the sum of every channel's net,
        # including the no-fee ManoMano channel (£9,395.32).
        expected = sum(ch["net"] for ch in self.data["channels"])
        mp_net = self._eval(SHEET_MARKETPLACE, "D", self.anchors["mp_grand_net_row"])
        self.assertAlmostEqual(mp_net, expected, delta=0.02)

    def test_contribution_identity(self):
        """Contribution must equal net minus combined deductions."""
        a = self.anchors["summary"]
        net  = self._eval(SHEET_SUMMARY, "B", a["net_row"])
        ded  = self._eval(SHEET_SUMMARY, "B", a["deductions_row"])
        con  = self._eval(SHEET_SUMMARY, "B", a["contribution_row"])
        self.assertAlmostEqual(con, net - ded, delta=0.001)


if __name__ == "__main__":
    unittest.main()
