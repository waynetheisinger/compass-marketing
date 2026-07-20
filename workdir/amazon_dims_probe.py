from scripts.amazon_client import AmazonClient
client = AmazonClient()
rows = client.fetch_report_rows("GET_MERCHANT_LISTINGS_ALL_DATA")
print("num rows:", len(rows))
if rows:
    print("columns:", list(rows[0].keys()))
    for r in rows[:3]:
        print({k: r.get(k) for k in ("seller-sku","asin1","item-name","status")})
