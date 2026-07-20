import os, sys, json
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.getcwd(), ".env"))
from scripts.mirakl_client import MiraklClient

c = MiraklClient("KINGFISHER")

# One full offer to learn the field names.
sample = c.get("/offers", params={"max": 1})["offers"][0]
print("=== sample offer field keys ===")
print(", ".join(sorted(sample.keys())))
print("\n=== sample offer (trimmed) ===")
print(json.dumps({k: sample[k] for k in sample
                  if k not in ("all_prices", "applicable_pricing", "offer_additional_fields")},
                 indent=2, ensure_ascii=False)[:1500])
