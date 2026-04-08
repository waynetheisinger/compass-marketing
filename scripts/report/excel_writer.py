"""
Excel spreadsheet builder for the monthly spend report.

Produces a 5-tab .xlsx matching the structure validated in
reports/marketing_spend_mock_2026-03.xlsx:

  Tab 1 — Summary
  Tab 2 — Marketplace Fees & Commissions
  Tab 3 — FBA Cost of Sales (Amazon only)
  Tab 4 — Ad Spend
  Tab 5 — Raw Data (sample transaction rows)

Usage:
    from scripts.report.excel_writer import build_workbook
    wb = build_workbook(report_data, month_label="March 2026")
    wb.save("reports/marketing_spend_2026-03.xlsx")
"""
from __future__ import annotations

from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ---------------------------------------------------------------------------
# Colour palette (matches mock_report.py)
# ---------------------------------------------------------------------------
DARK_GREEN  = "1B5E20"
MID_GREEN   = "2E7D32"
LIGHT_GREEN = "C8E6C9"
PALE_GREEN  = "E8F5E9"
DARK_BLUE   = "0D47A1"
MID_BLUE    = "1565C0"
LIGHT_BLUE  = "BBDEFB"
PALE_BLUE   = "E3F2FD"
AMBER       = "FFF8E1"
WHITE       = "FFFFFF"
DARK_TEXT   = "212121"
MUTED_TEXT  = "757575"

GBP = "£#,##0.00"
PCT = "0.0%"
INT = "#,##0"


# ---------------------------------------------------------------------------
# Low-level style helpers
# ---------------------------------------------------------------------------

def _fill(hex_colour: str) -> PatternFill:
    return PatternFill("solid", fgColor=hex_colour)


def _font(bold=False, size=10, colour=DARK_TEXT, italic=False) -> Font:
    return Font(bold=bold, size=size, color=colour, italic=italic)


def _border_bottom(colour="E0E0E0") -> Border:
    return Border(bottom=Side(style="thin", color=colour))


def _set_col_widths(ws, widths: list[float]) -> None:
    for col, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = w


# ---------------------------------------------------------------------------
# Higher-level cell writers
# ---------------------------------------------------------------------------

def _title(ws, text: str, ncols: int, bg=DARK_GREEN) -> None:
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    ws.row_dimensions[1].height = 34
    c = ws["A1"]
    c.value = text
    c.fill = _fill(bg)
    c.font = Font(bold=True, size=13, color=WHITE)
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)


def _header_row(ws, row: int, values: list, bg=DARK_GREEN, fg=WHITE) -> None:
    ws.row_dimensions[row].height = 22
    for col, val in enumerate(values, 1):
        c = ws.cell(row=row, column=col, value=val)
        c.fill = _fill(bg)
        c.font = Font(bold=True, size=10, color=fg)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _cell(ws, row: int, col: int, value, fmt=None, bold=False,
          bg=WHITE, align="right", fg=DARK_TEXT, italic=False):
    c = ws.cell(row=row, column=col, value=value)
    if fmt:
        c.number_format = fmt
    c.font = Font(bold=bold, size=10, color=fg, italic=italic)
    c.fill = _fill(bg)
    c.alignment = Alignment(horizontal=align, vertical="center")
    c.border = _border_bottom()
    return c


def _section_heading(ws, row: int, text: str, ncols: int, bg=MID_GREEN) -> None:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
    ws.row_dimensions[row].height = 20
    c = ws.cell(row=row, column=1, value=text)
    c.fill = _fill(bg)
    c.font = Font(bold=True, size=10, color=WHITE)
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)


def _subtotal_row(ws, row: int, ncols: int, label: str,
                  col_values: dict, bg=LIGHT_GREEN, fg=DARK_TEXT) -> None:
    for col in range(1, ncols + 1):
        ws.cell(row=row, column=col).fill = _fill(bg)
    _cell(ws, row, 1, label, align="right", bg=bg, bold=True, fg=fg)
    for col, (val, fmt) in col_values.items():
        _cell(ws, row, col, val, fmt=fmt, bg=bg, bold=True, fg=fg)


def _grand_total_row(ws, row: int, ncols: int, label: str,
                     col_values: dict, bg=DARK_GREEN) -> None:
    ws.row_dimensions[row].height = 18
    for col in range(1, ncols + 1):
        ws.cell(row=row, column=col).fill = _fill(bg)
        ws.cell(row=row, column=col).font = Font(bold=True, size=10, color=WHITE)
    _cell(ws, row, 1, label, align="left", bg=bg, bold=True, fg=WHITE)
    for col, (val, fmt) in col_values.items():
        c = _cell(ws, row, col, val, fmt=fmt, bg=bg, bold=True, fg=WHITE)
        c.font = Font(bold=True, size=10, color=WHITE)


def _note_row(ws, row: int, ncols: int, text: str,
              bg=PALE_BLUE, fg=DARK_BLUE) -> None:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
    ws.row_dimensions[row].height = 20
    c = ws.cell(row=row, column=1, value=text)
    c.fill = _fill(bg)
    c.font = Font(size=9, color=fg, italic=True)
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)


def _coverage_cell(ws, row: int, col: int, note: str | None) -> None:
    """Render a coverage/status cell — green COMPLETE or amber NOT CONNECTED."""
    if note is None:
        _cell(ws, row, col, "COMPLETE", align="center",
              bg="E8F5E9", fg=MID_GREEN, bold=True)
    elif note.startswith("PARTIAL"):
        _cell(ws, row, col, "PARTIAL", align="center",
              bg=AMBER, fg="E65100", bold=True)
    else:
        _cell(ws, row, col, "NOT CONNECTED", align="center",
              bg="FFF3E0", fg="BF360C", bold=True)


def _mock_banner(ws, row: int, ncols: int) -> None:
    """Amber banner shown when running in dry-run / mock mode."""
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
    ws.row_dimensions[row].height = 22
    c = ws.cell(row=row, column=1,
                value="⚠  MOCK / DRY-RUN DATA — replace with live API credentials before sharing with funders.")
    c.fill = _fill(AMBER)
    c.font = Font(bold=True, size=9, color="E65100")
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)


# ---------------------------------------------------------------------------
# Tab 1 — Summary
# ---------------------------------------------------------------------------

def _build_summary(wb: Workbook, data: dict, month_label: str) -> None:
    ws = wb.create_sheet("Summary")
    ws.sheet_view.showGridLines = False
    _set_col_widths(ws, [38, 18, 16, 44])
    _title(ws, f"MowDirect — Monthly Cost Report  |  {month_label}", 4)

    ws.row_dimensions[2].height = 4
    _header_row(ws, 3, ["Metric", "Amount (£)", "% of Gross Revenue", "Notes"])

    summary   = data["summary"]
    gross     = summary["gross"]

    metric_rows = [
        ("Gross Revenue (all channels)", gross, 1.0,
         "Shopify Direct + eBay + Amazon + ManoMano + OnBuy + B&Q"),
        None,
        ("Marketplace commissions & fees", summary["total_fees"],
         summary["total_fees"] / gross if gross else 0,
         "Referral fees, subscriptions, listing charges — see Marketplace Fees tab"),
        ("Amazon FBA cost of sales", summary["total_fba"],
         summary["total_fba"] / gross if gross else 0,
         "Fulfilment, storage, inbound, prep — see FBA Cost of Sales tab"),
        ("Total paid ad spend", summary["total_ads"],
         summary["total_ads"] / gross if gross else 0,
         "Google Ads + eBay Promoted Listings + Amazon Sponsored Products"),
        None,
        ("Total deductions (fees + FBA + ads)", summary["combined"],
         summary["combined"] / gross if gross else 0,
         "All costs combined"),
        ("Net contribution", summary["net"],
         summary["net"] / gross if gross else 0,
         "Gross revenue after all marketplace costs"),
    ]

    r = 4
    shade = [PALE_GREEN, WHITE]
    si = 0
    for m in metric_rows:
        if m is None:
            ws.row_dimensions[r].height = 8
            r += 1
            continue
        label, amount, pct, note = m
        is_key = label.startswith("Total deductions") or label.startswith("Net contribution")
        bg = LIGHT_GREEN if is_key else shade[si % 2]
        si += 1
        ws.row_dimensions[r].height = 18
        _cell(ws, r, 1, label, align="left", bg=bg, bold=is_key)
        _cell(ws, r, 2, amount, fmt=GBP,  bg=bg, bold=is_key)
        _cell(ws, r, 3, pct,   fmt=PCT,   bg=bg, bold=is_key)
        _cell(ws, r, 4, note,  align="left", bg=bg, fg=MUTED_TEXT, italic=True)
        r += 1

    r += 1

    # Commission by channel
    _section_heading(ws, r, "  Commissions & Fees by Channel", 4)
    r += 1
    _header_row(ws, r, ["Channel", "Gross Revenue (£)", "Commissions & Fees (£)", "Fee %"])
    r += 1
    for ch in data["channels"]:
        bg = PALE_GREEN if r % 2 == 0 else WHITE
        _cell(ws, r, 1, ch["name"], align="left", bg=bg, bold=True)
        _cell(ws, r, 2, ch["gross"],      fmt=GBP, bg=bg)
        _cell(ws, r, 3, ch["total_fees"], fmt=GBP, bg=bg)
        pct_val = ch["total_fees"] / ch["gross"] if ch["gross"] else 0
        _cell(ws, r, 4, pct_val, fmt=PCT, bg=bg)
        r += 1
    _subtotal_row(ws, r, 4, "TOTAL", {
        2: (gross, GBP),
        3: (summary["total_fees"], GBP),
        4: (summary["total_fees"] / gross if gross else 0, PCT),
    })
    r += 2

    # FBA summary
    amazon_gross = next((c["gross"] for c in data["channels"] if "Amazon" in c["name"]), 0)
    _section_heading(ws, r, "  Amazon FBA Cost of Sales", 4, bg=MID_BLUE)
    r += 1
    _header_row(ws, r, ["Cost Type", "Amount (£)", "% of Amazon Revenue", "Notes"],
                bg=DARK_BLUE)
    r += 1
    for row_data in data.get("fba_rows", []):
        bg = PALE_BLUE if r % 2 == 0 else WHITE
        _cell(ws, r, 1, row_data["label"], align="left", bg=bg)
        _cell(ws, r, 2, row_data["amount"], fmt=GBP, bg=bg)
        pct_val = row_data["amount"] / amazon_gross if amazon_gross else 0
        _cell(ws, r, 3, pct_val, fmt=PCT, bg=bg)
        _cell(ws, r, 4, "", bg=bg)
        r += 1
    _subtotal_row(ws, r, 4, "TOTAL FBA COST OF SALES", {
        2: (summary["total_fba"], GBP),
        3: (summary["total_fba"] / amazon_gross if amazon_gross else 0, PCT),
    }, bg=LIGHT_BLUE, fg=DARK_BLUE)
    r += 2

    # Ad spend summary
    _section_heading(ws, r, "  Ad Spend by Platform", 4)
    r += 1
    _header_row(ws, r, ["Platform", "Spend (£)", "% of Gross Revenue", "Note / Coverage"])
    r += 1
    for row_data in data.get("ad_spend_platform_summary", []):
        bg = PALE_GREEN if r % 2 == 0 else WHITE
        _cell(ws, r, 1, row_data["platform"], align="left", bg=bg, bold=True)
        _cell(ws, r, 2, row_data["spend"], fmt=GBP, bg=bg)
        pct_val = row_data["spend"] / gross if gross else 0
        _cell(ws, r, 3, pct_val, fmt=PCT, bg=bg)
        _coverage_cell(ws, r, 4, row_data.get("note"))
        r += 1
    _subtotal_row(ws, r, 4, "TOTAL", {
        2: (summary["total_ads"], GBP),
        3: (summary["total_ads"] / gross if gross else 0, PCT),
    })
    r += 2

    if data.get("is_mock"):
        _mock_banner(ws, r, 4)


# ---------------------------------------------------------------------------
# Tab 2 — Marketplace Fees & Commissions
# ---------------------------------------------------------------------------

def _build_marketplace(wb: Workbook, data: dict, month_label: str) -> None:
    ws = wb.create_sheet("Marketplace Fees & Commissions")
    ws.sheet_view.showGridLines = False
    _set_col_widths(ws, [24, 36, 14, 16, 14, 26, 16])
    _title(ws, f"Marketplace Commissions & Fees  |  {month_label}", 7)
    ws.row_dimensions[2].height = 4

    _header_row(ws, 3, [
        "Channel", "Fee Type", "Amount (£)", "Channel Revenue (£)",
        "Fee as % of Revenue", "Data Source", "Coverage",
    ])

    summary = data["summary"]
    gross   = summary["gross"]
    r = 4

    for ch in data["channels"]:
        ch_fees = ch["total_fees"]
        fee_rows = ch["fee_rows"]
        for j, row_data in enumerate(fee_rows):
            bg = PALE_GREEN if r % 2 == 0 else WHITE
            _cell(ws, r, 1, ch["name"] if j == 0 else "",
                  align="left", bg=bg, bold=(j == 0))
            _cell(ws, r, 2, row_data["label"], align="left", bg=bg)
            _cell(ws, r, 3, row_data["amount"], fmt=GBP, bg=bg)
            _cell(ws, r, 4, ch["gross"] if j == 0 else None,
                  fmt=GBP if j == 0 else "@", bg=bg)
            pct_val = row_data["amount"] / ch["gross"] if ch["gross"] else 0
            _cell(ws, r, 5, pct_val, fmt=PCT, bg=bg)
            _cell(ws, r, 6, ch["source"] if j == 0 else "",
                  align="left", bg=bg, fg=MUTED_TEXT, italic=True)
            if j == 0:
                _coverage_cell(ws, r, 7, ch.get("note"))
            else:
                _cell(ws, r, 7, "", bg=bg)
            r += 1

        _subtotal_row(ws, r, 7, f"{ch['name']} subtotal", {
            3: (ch_fees, GBP),
            4: (ch["gross"], GBP),
            5: (ch_fees / ch["gross"] if ch["gross"] else 0, PCT),
        })
        r += 1

    _grand_total_row(ws, r, 7, "GRAND TOTAL — MARKETPLACE FEES", {
        3: (summary["total_fees"], GBP),
        4: (gross, GBP),
        5: (summary["total_fees"] / gross if gross else 0, PCT),
    })
    r += 2

    _note_row(ws, r, 7,
              "ℹ  Amazon FBA operational costs (fulfilment, storage, inbound, prep) "
              "are excluded from this tab — see 'FBA Cost of Sales' tab.")
    r += 2

    if data.get("is_mock"):
        _mock_banner(ws, r, 7)


# ---------------------------------------------------------------------------
# Tab 3 — FBA Cost of Sales
# ---------------------------------------------------------------------------

def _build_fba(wb: Workbook, data: dict, month_label: str) -> None:
    ws = wb.create_sheet("FBA Cost of Sales")
    ws.sheet_view.showGridLines = False
    _set_col_widths(ws, [38, 14, 18, 20, 36])
    _title(ws, f"Amazon FBA — Cost of Sales  |  {month_label}", 5, bg=DARK_BLUE)
    ws.row_dimensions[2].height = 4

    # Explanation banner
    ws.merge_cells("A3:E3")
    ws.row_dimensions[3].height = 30
    c = ws["A3"]
    c.value = (
        "Operational costs charged by Amazon for physical fulfilment of orders via FBA "
        "(Fulfilment by Amazon). These are costs of sale, not marketing spend, and are "
        "tracked separately from the marketing budget."
    )
    c.fill = _fill(PALE_BLUE)
    c.font = Font(size=9, color=DARK_BLUE, italic=True)
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1, wrap_text=True)

    summary      = data["summary"]
    amazon_gross = next((c["gross"] for c in data["channels"] if "Amazon" in c["name"]), 0)
    gross        = summary["gross"]

    _header_row(ws, 4, [
        "FBA Cost Type", "Amount (£)",
        "% of Amazon Revenue", "% of Total Gross Revenue", "Notes",
    ], bg=DARK_BLUE)

    fba_notes: dict[str, str] = {
        "FBA fulfilment fees": "Charged per unit shipped. Varies by product size/weight tier.",
        "FBA fulfilment fees (per unit)": "Per-unit pick, pack & ship fee.",
        "FBA fulfilment fees (per order)": "Per-order handling fee.",
        "FBA storage fees": "Monthly charge based on cubic footage. Higher rates Oct–Dec.",
        "FBA inbound shipping": "Cost of sending stock from MowDirect/supplier to Amazon FC.",
        "FBA prep & labelling": "Per-unit labelling, bagging, or bundling required by Amazon.",
        "FBA long-term storage fees": "Applied to units stored > 365 days.",
        "FBA removal / disposal fees": "Charged when removing or disposing of FBA stock.",
    }

    r = 5
    amazon_note  = data.get("amazon_fba_note")
    fba_rows     = data.get("fba_rows", [])

    if amazon_note and not fba_rows:
        _coverage_cell(ws, r, 1, amazon_note)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=5)
        ws.cell(row=r, column=1).value = amazon_note
        ws.cell(row=r, column=1).alignment = Alignment(horizontal="left",
                                                        vertical="center", indent=1)
        r += 2
    else:
        for row_data in fba_rows:
            bg = PALE_BLUE if r % 2 == 0 else WHITE
            _cell(ws, r, 1, row_data["label"], align="left", bg=bg, bold=True)
            _cell(ws, r, 2, row_data["amount"], fmt=GBP, bg=bg)
            pct_amazon = row_data["amount"] / amazon_gross if amazon_gross else 0
            pct_total  = row_data["amount"] / gross if gross else 0
            _cell(ws, r, 3, pct_amazon, fmt=PCT, bg=bg)
            _cell(ws, r, 4, pct_total,  fmt=PCT, bg=bg)
            _cell(ws, r, 5, fba_notes.get(row_data["label"], ""),
                  align="left", bg=bg, fg=MUTED_TEXT, italic=True)
            r += 1

        _subtotal_row(ws, r, 5, "TOTAL FBA COST OF SALES", {
            2: (summary["total_fba"], GBP),
            3: (summary["total_fba"] / amazon_gross if amazon_gross else 0, PCT),
            4: (summary["total_fba"] / gross if gross else 0, PCT),
        }, bg=LIGHT_BLUE, fg=DARK_BLUE)
        r += 2

    # Amazon referral fee (shown for reference only)
    _section_heading(ws, r, "  For reference: Amazon referral fee — on Marketplace Fees tab", 5,
                     bg=MID_BLUE)
    r += 1
    _header_row(ws, r, ["Fee Type", "Amount (£)", "% of Amazon Revenue", "", "Notes"],
                bg=DARK_BLUE)
    r += 1
    amazon_ch    = next((c for c in data["channels"] if "Amazon" in c["name"]), None)
    ref_fee_amt  = 0.0
    if amazon_ch:
        ref_fee_amt = sum(
            fr["amount"] for fr in amazon_ch.get("fee_rows", [])
            if "referral" in fr["label"].lower() or "commission" in fr["label"].lower()
        )
    bg = PALE_BLUE
    _cell(ws, r, 1, "Referral fees / commission", align="left", bg=bg)
    _cell(ws, r, 2, ref_fee_amt, fmt=GBP, bg=bg)
    pct_val = ref_fee_amt / amazon_gross if amazon_gross else 0
    _cell(ws, r, 3, pct_val, fmt=PCT, bg=bg)
    _cell(ws, r, 4, "", bg=bg)
    _cell(ws, r, 5,
          "Commission on each sale — treated as marketing cost of channel, not FBA operational cost.",
          align="left", bg=bg, fg=MUTED_TEXT, italic=True)
    r += 2

    if data.get("is_mock"):
        _mock_banner(ws, r, 5)


# ---------------------------------------------------------------------------
# Tab 4 — Ad Spend
# ---------------------------------------------------------------------------

def _build_ad_spend(wb: Workbook, data: dict, month_label: str) -> None:
    ws = wb.create_sheet("Ad Spend")
    ws.sheet_view.showGridLines = False
    _set_col_widths(ws, [20, 38, 14, 14, 10, 10, 20])
    _title(ws, f"Paid Ad Spend  |  {month_label}", 7)
    ws.row_dimensions[2].height = 4

    _header_row(ws, 3, [
        "Platform", "Campaign", "Spend (£)", "Impressions",
        "Clicks", "CTR", "Coverage",
    ])

    summary  = data["summary"]
    gross    = summary["gross"]
    ad_rows  = data.get("ad_spend_rows", [])

    r = 4
    current_platform = None

    # Group rows by platform
    platforms: dict[str, list[dict]] = {}
    for row_data in ad_rows:
        p = row_data["platform"]
        platforms.setdefault(p, []).append(row_data)

    for platform, rows in platforms.items():
        platform_total = sum(r_["spend"] for r_ in rows)
        for j, row_data in enumerate(rows):
            bg = PALE_GREEN if r % 2 == 0 else WHITE
            _cell(ws, r, 1, platform if j == 0 else "",
                  align="left", bg=bg, bold=(j == 0))
            _cell(ws, r, 2, row_data["campaign_name"], align="left", bg=bg)
            _cell(ws, r, 3, row_data["spend"], fmt=GBP, bg=bg)
            impr = row_data.get("impressions")
            clicks = row_data.get("clicks")
            _cell(ws, r, 4, impr,   fmt=INT if impr   else "@", bg=bg)
            _cell(ws, r, 5, clicks, fmt=INT if clicks else "@", bg=bg)
            if impr and clicks:
                _cell(ws, r, 6, clicks / impr, fmt=PCT, bg=bg)
            else:
                _cell(ws, r, 6, "—", align="center", bg=bg, fg=MUTED_TEXT)
            _coverage_cell(ws, r, 7, data.get("ad_spend_notes", {}).get(platform))
            r += 1

        if len(rows) > 1:
            _subtotal_row(ws, r, 7, f"{platform} subtotal",
                          {3: (platform_total, GBP)})
            r += 1

    # Not-connected platforms
    for row_data in data.get("ad_spend_not_connected", []):
        bg = "FFF3E0"
        _cell(ws, r, 1, row_data["platform"], align="left", bg=bg, bold=True)
        _cell(ws, r, 2, "—", align="center", bg=bg, fg=MUTED_TEXT)
        _cell(ws, r, 3, "—", align="center", bg=bg, fg=MUTED_TEXT)
        _cell(ws, r, 4, "—", align="center", bg=bg, fg=MUTED_TEXT)
        _cell(ws, r, 5, "—", align="center", bg=bg, fg=MUTED_TEXT)
        _cell(ws, r, 6, "—", align="center", bg=bg, fg=MUTED_TEXT)
        _cell(ws, r, 7, "NOT CONNECTED", align="center", bg=AMBER, fg="BF360C", bold=True)
        r += 1

    r += 1
    _grand_total_row(ws, r, 7, "TOTAL AD SPEND", {3: (summary["total_ads"], GBP)})
    r += 2

    if data.get("is_mock"):
        _mock_banner(ws, r, 7)


# ---------------------------------------------------------------------------
# Tab 5 — Raw Data
# ---------------------------------------------------------------------------

def _build_raw(wb: Workbook, data: dict, month_label: str) -> None:
    ws = wb.create_sheet("Raw Data")
    ws.sheet_view.showGridLines = False
    _set_col_widths(ws, [20, 14, 36, 14, 22, 22])
    _title(ws, f"Raw Data — Transactions  |  {month_label}", 6)
    ws.row_dimensions[2].height = 4

    r = 3

    def write_block(heading: str, col_headers: list[str], rows: list[dict],
                    col_map: list[str], bg_head=MID_GREEN, bg_even=PALE_GREEN) -> int:
        nonlocal r
        _section_heading(ws, r, f"  {heading}", 6, bg=bg_head)
        _header_row(ws, r + 1, col_headers, bg=bg_head)
        r += 2
        for row_data in rows:
            bg = bg_even if r % 2 == 0 else WHITE
            is_ellipsis = str(row_data.get(col_map[0], "")).startswith("…")
            for col_i, key in enumerate(col_map, 1):
                val = row_data.get(key, "")
                c = ws.cell(row=r, column=col_i, value=val)
                c.fill = _fill(bg)
                c.font = Font(size=10, color=MUTED_TEXT if is_ellipsis else DARK_TEXT,
                              italic=is_ellipsis)
                c.alignment = Alignment(
                    horizontal="right" if col_i == 4 and isinstance(val, float) else "left",
                    vertical="center")
                if col_i == 4 and isinstance(val, float):
                    c.number_format = GBP
                c.border = _border_bottom()
            r += 1
        r += 1
        return r

    # eBay
    ebay_raw = data.get("ebay_raw_transactions", [])[:20]
    if ebay_raw:
        write_block(
            "eBay Finance Transactions",
            ["Transaction ID", "Date", "Type", "Amount (£)", "Order ID", "Source"],
            [
                {
                    "id":     t.get("transactionId", ""),
                    "date":   t.get("transactionDate", "")[:10] if t.get("transactionDate") else "",
                    "type":   t.get("transactionType", ""),
                    "amount": float(t.get("amount", {}).get("value", 0)),
                    "order":  t.get("orderId", "—"),
                    "source": "eBay Finances API",
                }
                for t in ebay_raw
            ],
            ["id", "date", "type", "amount", "order", "source"],
        )

    # Amazon
    amazon_raw = data.get("amazon_raw_fees", [])[:20]
    if amazon_raw:
        write_block(
            "Amazon Settlement Line Items",
            ["Settlement ID", "Posted Date", "Fee Type", "Amount (£)", "Order ID", "Source"],
            amazon_raw,
            ["settlement_id", "posted_at", "fee_type", "amount", "order_id", "source"],
            bg_head=DARK_BLUE, bg_even=PALE_BLUE,
        )

    # BaseLinker
    bl_raw = data.get("baselinker_raw_orders", [])[:20]
    if bl_raw:
        write_block(
            "BaseLinker Orders (ManoMano / OnBuy)",
            ["Order ID", "Date", "Channel", "Order Total (£)", "Commission (£)", "Source"],
            [
                {
                    "id":         o.get("order_id", ""),
                    "date":       o.get("date_add", ""),
                    "channel":    o.get("order_source", ""),
                    "total":      float(o.get("price_gross", 0) or 0),
                    "commission": float(o.get("commission_amount", 0) or 0),
                    "source":     "BaseLinker",
                }
                for o in bl_raw
            ],
            ["id", "date", "channel", "total", "commission", "source"],
        )

    # Google Ads
    google_raw = data.get("google_ads_raw", [])
    if google_raw:
        write_block(
            "Google Ads — Campaign Spend",
            ["Campaign ID", "Type", "Campaign Name", "Spend (£)", "Impressions", "Clicks"],
            [
                {
                    "id":    r_["campaign_id"],
                    "type":  r_["campaign_type"],
                    "name":  r_["campaign_name"],
                    "spend": float(r_["spend_gbp"]),
                    "impr":  r_["impressions"],
                    "click": r_["clicks"],
                }
                for r_ in google_raw
            ],
            ["id", "type", "name", "spend", "impr", "click"],
        )

    if data.get("is_mock"):
        _mock_banner(ws, r, 6)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_workbook(report_data: dict, month_label: str) -> Workbook:
    """
    Build the complete 5-tab workbook from assembled report_data.

    report_data keys (all optional — missing data renders as NOT CONNECTED):
        summary              — from transforms.build_summary()
        channels             — list of channel dicts with fee_rows
        fba_rows             — list of FBA cost dicts
        amazon_fba_note      — coverage note for Amazon FBA tab
        ad_spend_rows        — list of ad spend dicts
        ad_spend_not_connected — list of {platform, note} for missing platforms
        ad_spend_platform_summary — [{platform, spend, note}] for Summary tab
        ad_spend_notes       — {platform: note} for coverage cells
        ebay_raw_transactions
        amazon_raw_fees
        baselinker_raw_orders
        google_ads_raw
        is_mock              — if True, adds amber banner to all tabs
    """
    wb = Workbook()
    wb.remove(wb.active)

    _build_summary(wb, report_data, month_label)
    _build_marketplace(wb, report_data, month_label)
    _build_fba(wb, report_data, month_label)
    _build_ad_spend(wb, report_data, month_label)
    _build_raw(wb, report_data, month_label)

    return wb
