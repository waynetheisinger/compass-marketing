"""
Pull spend + conversion value for the key comparison windows so the deck
can show like-for-like improvement vs April.
"""
from datetime import date
from scripts.google_ads_client import GoogleAdsClient

client = GoogleAdsClient()


def window(label, start, end):
    rows = client.get_campaign_spend(start, end)
    spend = sum(r["spend_gbp"] for r in rows)
    conv  = sum(r["conversions_value"] for r in rows)
    days  = (end - start).days + 1
    print(
        f"{label:<40}  spend £{spend:>10,.2f}   "
        f"conv £{conv:>10,.2f}   ROAS {(conv/spend if spend else 0):>5.2f}x   "
        f"£/day £{spend/days:>7,.2f}"
    )


print(f"{'Window':<40}  {'spend':>14}   {'conv val':>14}   {'ROAS':>10}   {'£/day':>10}")
print("-" * 100)
window("April (1–30 Apr)",       date(2026, 4, 1),  date(2026, 4, 30))
window("May 1–10",                date(2026, 5, 1),  date(2026, 5, 10))
window("This week (Mon 4 – Sun 10 May)", date(2026, 5, 4), date(2026, 5, 10))
window("1–9 May (the email window)",     date(2026, 5, 1), date(2026, 5, 9))
