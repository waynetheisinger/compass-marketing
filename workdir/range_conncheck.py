import os, sys
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.getcwd(), ".env"))

for k in ("MIRAKL_THERANGE_BASE_URL", "MIRAKL_THERANGE_API_KEY", "MIRAKL_THERANGE_CHANNEL"):
    v = os.environ.get(k)
    shown = (v[:24] + "…") if (v and "KEY" not in k) else ("<set>" if v else "<MISSING>")
    print(f"{k:32} {shown}")
print()

from scripts.mirakl_client import MiraklClient
c = MiraklClient("THERANGE")

for ep in ("/account", "/version"):
    try:
        r = c.get(ep)
        print(f"=== GET {ep} OK ===")
        if ep == "/account":
            for key in ("shop_id", "shop_name", "currency_iso_code", "premium_state", "state"):
                if key in r:
                    print(f"  {key}: {r[key]}")
        else:
            print(" ", r)
    except Exception as e:
        print(f"GET {ep} failed: {type(e).__name__}: {e}")
