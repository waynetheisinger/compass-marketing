import os, sys, json
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.getcwd(), ".env"))
from scripts.mirakl_client import MiraklClient

c = MiraklClient("KINGFISHER")

# List product import trackings (most recent first if the API honours it).
imports = c.get("/products/imports", params={"max": 100})
items = imports.get("product_imports") or imports.get("imports") or imports
print("=== raw keys ===", list(imports.keys()) if isinstance(imports, dict) else type(imports))
if isinstance(items, list):
    print(f"=== {len(items)} import trackings ===")
    for it in items:
        print(json.dumps({k: it.get(k) for k in
              ("import_id", "id", "status", "date_created", "has_error_report",
               "lines_in_success", "lines_in_error", "lines_read")}, ensure_ascii=False))
else:
    print(json.dumps(imports, indent=2, ensure_ascii=False)[:3000])
