import os, json
from scripts.amazon_client import AmazonClient
client = AmazonClient()
seller_id = os.environ["AMAZON_SELLER_ID"]
sku = "100134O"
data = client.get(f"/listings/2021-08-01/items/{seller_id}/{sku}",
    params={"marketplaceIds": client.marketplace_id, "includedData": "attributes"})
attrs = data.get("attributes", {})
print("ALL ATTR KEYS:")
for k in sorted(attrs.keys()):
    print(" ", k)
print("\nDIMENSION-RELATED:")
for k in sorted(attrs.keys()):
    if any(t in k for t in ("dimension","weight","length","width","height")):
        print(k, "=", json.dumps(attrs[k]))
