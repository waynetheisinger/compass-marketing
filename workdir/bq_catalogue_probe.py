import os, sys, json
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.getcwd(), ".env"))
from scripts.mirakl_client import MiraklClient

c = MiraklClient("KINGFISHER")

def probe(method, ep, **params):
    url = f"{c._base_url}{ep}"
    try:
        r = c._session.request(method, url, params=params or None)
        body = r.text
        snippet = body[:600]
        # try to surface top-level keys / counts if JSON
        meta = ""
        try:
            j = r.json()
            if isinstance(j, dict):
                meta = " keys=" + ",".join(list(j.keys())[:8])
                for ck in ("total_count", "total", "count"):
                    if ck in j:
                        meta += f" {ck}={j[ck]}"
        except Exception:
            pass
        print(f"[{r.status_code}] {method} {ep} {params}{meta}")
        print("   ", snippet.replace(chr(10), " ")[:400])
    except Exception as e:
        print(f"[ERR] {method} {ep}: {type(e).__name__}: {e}")
    print()

# Candidate catalogue-listing endpoints (seller API)
probe("GET", "/products", max=5)
probe("GET", "/products")
probe("GET", "/products/exports")
probe("GET", "/products/export")
# Offers for comparison (the layer on top)
probe("GET", "/offers", max=5)
