"""
Push approved Amazon SBS listing rewrites via SP-API patchListingsItem.

Source of truth: reports/amazon_sbs_rewrite_drafts.md (approved 2026-04-25).

Usage:
    python3.11 scripts/amazon_sbs_push.py --sku SBS460CLM
    python3.11 scripts/amazon_sbs_push.py --remaining   # all except SBS460CLM
    python3.11 scripts/amazon_sbs_push.py --all
"""
import os
import sys
import json
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from amazon_client import AmazonClient, AmazonSPAPIError

LANG        = "en_GB"
MARKETPLACE = "A1F83G8C2ARO7P"
SELLER_ID   = os.environ["AMAZON_SELLER_ID"]


LISTINGS = {
    "SBS460CLM": {
        "title": "SPECTRUM SBS460CLM 40V Cordless Self-Propelled Lawn Mower, 46cm Cut, Brushless Motor, 7 Cutting Heights (25–75mm), 60L Grass Bag, 4-in-1 Disposal — Bare Tool (Battery & Charger Sold Separately)",
        "bullets": [
            "SELF-PROPELLED DRIVE: Transmission does the walking for you — ideal for medium-to-large lawns and uneven ground where a push-along becomes hard work.",
            "46CM BRUSHLESS CUTTING DECK: High-efficiency brushless motor runs a 46cm (18\") steel deck at consistent speed — handles thick grass without bogging down or losing pace.",
            "SEVEN CUTTING HEIGHTS: Single central lever adjusts 25mm to 75mm in seven steps — scalp-close in spring, lush finish in summer. Seconds to change, no tools needed.",
            "4-IN-1 GRASS DISPOSAL: Rear-collect into a 60-litre bag (with bag-full indicator), mulch, rear-discharge, or side-discharge — one tool for every lawn management style.",
            "BARE TOOL — BATTERY & CHARGER SOLD SEPARATELY: Takes two Spectrum 40V batteries (SBS20CB 2.0Ah or SBS40CB 4.0Ah). Pair with an SBSCBC, SBSCSC or SBSCDC charger.",
        ],
        "description": (
            "The Spectrum SBS460CLM is a cordless self-propelled lawn mower built for medium-to-large gardens that demand more than a basic push-along. A high-efficiency brushless motor, powered by a dual 40V battery configuration, keeps cutting speed consistent from the first strip to the last — even in thicker grass.\n\n"
            "The 46cm (18\") steel cutting deck covers ground quickly without compromising finish. Seven cutting heights from 25mm to 75mm adjust in seconds via a single central lever — scalp-close in early spring, a lush longer cut through summer heat.\n\n"
            "Grass disposal, four ways: rear collection into the 60-litre bag (with a bag-full indicator so there's no guesswork), mulching back into the lawn, rear discharge, or side discharge. Large ball-bearing wheels — 7\" front, 10\" rear — roll smoothly over uneven ground and resist turf damage.\n\n"
            "This is a bare-tool model. The mower takes two Spectrum 40V lithium-ion batteries, sold separately: SBS20CB (2.0Ah, lighter sessions) or SBS40CB (4.0Ah, longer runtime and consistent voltage under load). Compatible chargers are also sold separately: SBSCBC (standard 0.5A overnight), SBSCSC (fast 2A single bay), or SBSCDC (dual-bay 2A for rotating two batteries through one charger).\n\n"
            "Part of the Spectrum 40V cordless garden system — share batteries across our hedge trimmers, pole trimmers, and leaf blower vacs. Backed by a 5-year warranty."
        ),
    },
    "SBS480CBV": {
        "title": "SPECTRUM SBS480CBV 40V Cordless Leaf Blower Vacuum, 3-in-1 Blow/Vac/Mulch, 52m/s Airspeed, 12L Bag, 30:1 Mulch Ratio — Bare Tool (Battery & Charger Sold Separately)",
        "bullets": [
            "3-IN-1 BLOW, VAC, MULCH: One tool for autumn leaves, post-mow clippings, and damp debris — no cables, no compromise, no exhaust fumes.",
            "52M/S AIR SPEED: Blower mode delivers 52m/s at 264m³/h — shifts stubborn wet leaves from paths, patios, and lawn edges in quick passes.",
            "30:1 MULCH RATIO: Vacuum mode jumps to 588m³/h, pulling debris into the 12-litre bag while a 3-blade mulcher shreds it to a fraction of its original volume — fewer compost trips.",
            "4.26KG COMFORT: Lightweight build with adjustable handle keeps extended sessions fatigue-free. Reliable brush motor gives consistent airflow through the charge.",
            "BARE TOOL — BATTERY & CHARGER SOLD SEPARATELY: Compatible with Spectrum 40V batteries (SBS20CB 2.0Ah, SBS40CB 4.0Ah) and all Spectrum 40V chargers (SBSCBC, SBSCSC, SBSCDC).",
        ],
        "description": (
            "The Spectrum SBS480CBV is a 40V cordless blower vacuum that handles three jobs in one tool: blow, vacuum, and mulch. From autumn leaves and post-mow clippings to damp debris in hard-to-reach corners, no cables means no compromise on where you can work.\n\n"
            "In blower mode, the SBS480CBV generates an air speed of up to 52m/s with an air volume of 264m³/h — enough to shift stubborn wet leaves from paths, patios, and lawn edges in quick, efficient passes. Switch to vacuum mode and the air volume jumps to 588m³/h, pulling debris into the 12-litre collection bag while the built-in 3-blade mulching system shreds it at a 30:1 ratio. A full bag of leaves compresses down to a fraction of its original volume — fewer trips to the compost heap, more time getting on with the job.\n\n"
            "Weighing just 4.26kg with an adjustable handle, the SBS480CBV stays comfortable during longer sessions. A reliable brush motor delivers consistent airflow without the noise and exhaust of petrol alternatives.\n\n"
            "This is a bare-tool model. Compatible with all Spectrum 40V lithium-ion batteries, sold separately: SBS20CB (2.0Ah) or SBS40CB (4.0Ah). Compatible chargers are also sold separately: SBSCBC (standard 0.5A overnight), SBSCSC (fast 2A), or SBSCDC (dual-bay 2A).\n\n"
            "Part of the Spectrum 40V cordless garden system — share batteries across our lawn mowers, hedge trimmers, and pole trimmers. Backed by Spectrum's 5-year warranty."
        ),
    },
    "SBS560CHT": {
        "title": "SPECTRUM SBS560CHT 40V Cordless Hedge Trimmer, 45cm Laser-Cut Blade, Brushless Motor, 2,600 Cuts/Min, 18mm Capacity, 90° Rotating Handle — Bare Tool (Battery & Charger Sold Separately)",
        "bullets": [
            "BRUSHLESS MOTOR: Runs cooler, lasts longer, and extracts more runtime per charge than brush-motor trimmers. Keeps cutting speed steady through the battery.",
            "45CM LASER-CUT BLADE: 2,600 cuts per minute moves through established hedges quickly — sessions stay short and results stay consistent.",
            "18MM CUTTING CAPACITY: Handles the thicker, woodier growth in mature hedges without stalling or snagging — no need to drop down to loppers for branch-level stems.",
            "90° ROTATING HANDLE: Switch between horizontal top cuts and vertical face cuts without adjusting your stance — reduces wrist and shoulder strain on longer sessions.",
            "2.91KG BARE TOOL — BATTERY & CHARGER SOLD SEPARATELY: Compatible with Spectrum 40V batteries (SBS20CB 2.0Ah, SBS40CB 4.0Ah) and all Spectrum 40V chargers (SBSCBC, SBSCSC, SBSCDC).",
        ],
        "description": (
            "Neat hedges don't have to mean hard work. The Spectrum SBS560CHT cordless hedge trimmer combines a powerful brushless motor with a precision laser-cut blade for clean, professional-looking results with minimal fatigue.\n\n"
            "The brushless motor is a meaningful upgrade over brush-motor alternatives — it runs more efficiently, generates less heat, lasts longer between services, and extracts more runtime from each battery charge. At 2,600 cuts per minute, the 45cm cutting blade moves through foliage quickly, keeping trimming sessions short and results consistent.\n\n"
            "With an 18mm cutting capacity, the SBS560CHT handles the thicker, woodier growth found in established hedges without stalling or snagging. The 90° rotating handle is a genuine practical feature — it lets you switch between horizontal top cuts and vertical face cuts without adjusting your stance or grip, reducing strain on wrists and shoulders during longer sessions.\n\n"
            "At just 2.91kg, it's light enough to hold overhead comfortably for as long as the job demands, and the 40V Spectrum battery platform means it shares power with your other Spectrum cordless tools.\n\n"
            "This is a bare-tool model. Compatible with all Spectrum 40V lithium-ion batteries, sold separately: SBS20CB (2.0Ah) or SBS40CB (4.0Ah). Compatible chargers are also sold separately: SBSCBC (standard 0.5A overnight), SBSCSC (fast 2A), or SBSCDC (dual-bay 2A).\n\n"
            "Part of the Spectrum 40V cordless garden system — share batteries across our lawn mowers, pole trimmers, and leaf blower vacs. Backed by Spectrum's 5-year warranty."
        ),
    },
    "SBS240CPHT": {
        "title": "SPECTRUM SBS240CPHT 40V Cordless Pole Hedge Trimmer, 2.4m Reach, 46cm Dual-Action Laser-Cut Blade, 180° Adjustable Head, 18mm Capacity — Bare Tool (Battery & Charger Sold Separately)",
        "bullets": [
            "2.4M GROUND REACH: Handles tall hedges and high-sided borders safely from the ground — no ladder, no compromised balance.",
            "180° ADJUSTABLE HEAD: Trim horizontally along the top of a hedge or vertically down the face without awkward repositioning — get the angle ladders can't reach.",
            "46CM DUAL-ACTION BLADE: Laser-cut blade runs at 2,600 strokes per minute, slicing through branches up to 18mm thick with clean cuts that promote healthy regrowth.",
            "3.62KG LIGHTWEIGHT BUILD: Well-balanced at extension, reducing shoulder and arm fatigue on longer hedgerows. Reliable brush motor holds cutting speed through the charge.",
            "BARE TOOL — BATTERY & CHARGER SOLD SEPARATELY: Compatible with Spectrum 40V batteries (SBS20CB 2.0Ah, SBS40CB 4.0Ah) and all Spectrum 40V chargers (SBSCBC, SBSCSC, SBSCDC). 5-year warranty.",
        ],
        "description": (
            "Tall hedges and high-sided borders are no match for the Spectrum SBS240CPHT cordless pole hedge trimmer. With a total reach of 2.4 metres and a 180° adjustable cutting head, it handles the angles that ladders and standard trimmers can't — safely, from the ground.\n\n"
            "The 46cm dual-action, laser-cut blade runs at 2,600 strokes per minute, slicing through branches up to 18mm thick with clean, precise cuts that promote healthy regrowth. Whether you're trimming horizontally along the top of a hedge or working vertically down the face, the rotating head gives you the position you need without awkward repositioning.\n\n"
            "Power comes from Spectrum's 40V lithium-ion battery system. The reliable brush motor maintains consistent cutting speed throughout the charge, and at just 3.62kg the tool stays well-balanced and fatigue-free even on longer hedgerows.\n\n"
            "This is a bare-tool model. Compatible with all Spectrum 40V lithium-ion batteries, sold separately: SBS20CB (2.0Ah) or SBS40CB (4.0Ah). Compatible chargers are also sold separately: SBSCBC (standard 0.5A overnight), SBSCSC (fast 2A), or SBSCDC (dual-bay 2A).\n\n"
            "Part of the Spectrum 40V cordless garden system — share batteries across our lawn mowers, hedge trimmers, and leaf blower vacs. Backed by Spectrum's 5-year manufacturer's warranty."
        ),
    },
    "SBS40CB": {
        "title": "SPECTRUM SBS40CB 40V 4.0Ah Lithium-Ion Battery Pack, 21700 Cells, Thermal Protection, Compatible with All Spectrum 40V Cordless Tools & Chargers",
        "bullets": [
            "4.0AH CAPACITY: Double the runtime of the 2.0Ah pack — the right choice for larger gardens, longer sessions, or higher-draw tools like cordless mowers.",
            "21700 LITHIUM-ION CELLS: High-grade cells deliver steady voltage through the full discharge — no fade, no sudden drop-off, consistent tool performance to the end.",
            "900G, COMPACT FIT: Slots into every Spectrum 40V tool without adapters. Well-balanced so your tool stays nimble, not nose-heavy.",
            "THERMAL PROTECTION: Built-in circuitry protects cells against overheating, over-discharge, and short circuits — engineered for season-after-season use.",
            "FITS ALL SPECTRUM 40V TOOLS: Powers SBS460CLM lawn mower, SBS480CBV blower vac, SBS560CHT hedge trimmer, SBS240CPHT pole trimmer. Charges with SBSCBC, SBSCSC, or SBSCDC (all sold separately).",
        ],
        "description": (
            "Get the most from your Spectrum cordless tools with the SBS40CB 40V 4.0Ah lithium-ion battery pack. Built around high-grade 21700 lithium-ion cells, this battery delivers steady, reliable voltage throughout the discharge cycle — no fade, no sudden drop-off, just consistent performance right to the end.\n\n"
            "At 4.0Ah it holds double the capacity of the 2.0Ah option, making it the better choice for larger gardens, longer sessions, and higher-draw tools like cordless mowers. Whether you're mowing, trimming, or clearing leaves, the SBS40CB keeps your tools running longer between charges.\n\n"
            "The battery is compact and well-balanced at just 900g. It slots into any compatible Spectrum 40V tool without adapters or fuss, and charges with any Spectrum 40V charger — standard, fast, or dual.\n\n"
            "Robust construction, thermal protection circuitry, and Spectrum's proven build quality mean this battery is engineered to last season after season.\n\n"
            "Compatible tools (sold separately): SBS460CLM cordless lawn mower, SBS480CBV cordless blower vacuum, SBS560CHT cordless hedge trimmer, SBS240CPHT cordless pole hedge trimmer. Compatible chargers (sold separately): SBSCBC (standard 0.5A overnight), SBSCSC (fast 2A single-bay), SBSCDC (fast 2A dual-bay).\n\n"
            "Part of the Spectrum 40V cordless garden system. Backed by Spectrum's 5-year warranty."
        ),
    },
    "SBS20CB": {
        "title": "SPECTRUM SBS20CB 40V 2.0Ah Lithium-Ion Battery Pack, 18650 Cells, 710g Compact Size, Compatible with All Spectrum 40V Cordless Tools & Chargers",
        "bullets": [
            "2.0AH CAPACITY: Entry-level Spectrum battery ideal for lighter tools and shorter sessions — trimming, blowing, a quick tidy-up of a small lawn.",
            "18650 LITHIUM-ION CELLS: High-quality cells deliver consistent, stable voltage throughout the discharge — no dropoff, no hesitation, just reliable power.",
            "710G POCKET-SIZED: Compact pack keeps your tool nimble and easy to handle. A sensible spare to your 4.0Ah pack, or a dedicated battery for lighter jobs.",
            "AFFORDABLE PLATFORM ENTRY: Extend your Spectrum cordless toolkit without committing to higher-capacity packs — perfect second-battery for swap-and-go runtime.",
            "FITS ALL SPECTRUM 40V TOOLS: Powers SBS460CLM, SBS480CBV, SBS560CHT, SBS240CPHT. Charges with SBSCBC, SBSCSC, or SBSCDC (all sold separately).",
        ],
        "description": (
            "The Spectrum SBS20CB 40V 2.0Ah battery pack is the compact, lightweight entry point to Spectrum's 40V cordless system. Built with high-quality 18650 lithium-ion cells, it delivers consistent, stable voltage throughout the discharge cycle — no dropoff, no hesitation, just reliable power from start to finish.\n\n"
            "At 2.0Ah it's ideally suited to lighter tools and shorter sessions: trimming, blowing, a quick tidy-up of a small lawn. At just 710g, it keeps your tool feeling nimble and easy to handle, with no unnecessary bulk.\n\n"
            "As a spare to a 4.0Ah pack, or as a dedicated battery for your lighter Spectrum tools, the SBS20CB offers an affordable way to extend your cordless system without compromise. It fits every Spectrum 40V tool and charges with any Spectrum 40V charger.\n\n"
            "Compatible tools (sold separately): SBS460CLM cordless lawn mower, SBS480CBV cordless blower vacuum, SBS560CHT cordless hedge trimmer, SBS240CPHT cordless pole hedge trimmer. Compatible chargers (sold separately): SBSCBC (standard 0.5A overnight), SBSCSC (fast 2A single-bay), SBSCDC (fast 2A dual-bay).\n\n"
            "For higher-draw tools or longer sessions, consider upgrading to the Spectrum SBS40CB 4.0Ah battery pack, sold separately.\n\n"
            "Part of the Spectrum 40V cordless garden system. Backed by Spectrum's 5-year warranty."
        ),
    },
    "SBSCDC": {
        "title": "SPECTRUM SBSCDC 40V Dual Battery Fast Charger, 2A Per Port, Simultaneous Charging, Compatible with All Spectrum 40V Lithium-Ion Batteries",
        "bullets": [
            "DUAL-BAY FAST CHARGING: Charge two Spectrum 40V lithium-ion batteries simultaneously, both at full 2A output — no waiting idle while tools sit unused.",
            "2A PER PORT: Replenishes a 2.0Ah battery in roughly an hour and a 4.0Ah pack in around two. Run one tool continuously by rotating two batteries through the charger.",
            "INDEPENDENT MONITORING: Each port is managed separately — cuts off automatically at full capacity to protect battery health, regardless of which pack finishes first.",
            "COMPACT WORKBENCH FIT: 750g unit sits neatly on a shelf, in a tool store, or on a workbench. Easy to move between charging locations.",
            "FITS ALL SPECTRUM 40V BATTERIES: Works with SBS20CB (2.0Ah) and SBS40CB (4.0Ah). Powers Spectrum 40V cordless tools — SBS460CLM, SBS480CBV, SBS560CHT, SBS240CPHT (sold separately).",
        ],
        "description": (
            "When one battery isn't enough to get through the job, the Spectrum SBSCDC dual battery charger keeps you moving. Charge two Spectrum 40V lithium-ion batteries simultaneously — both at a full 2A output — so you're never waiting idle while your tools sit unused.\n\n"
            "At 2A per port, it replenishes a 2.0Ah battery in roughly an hour and a 4.0Ah pack in around two. Run one tool continuously by rotating two batteries through the charger, or simply top up your full kit in a single charging session.\n\n"
            "The unit is compact for a dual charger and sits neatly on a workbench, shelf, or in a tool store. Built-in charge management monitors each port independently, cutting off automatically at full capacity to protect battery health regardless of which battery finishes first.\n\n"
            "Compatible with all Spectrum 40V lithium-ion batteries, sold separately: SBS20CB (2.0Ah) and SBS40CB (4.0Ah).\n\n"
            "Powers the Spectrum 40V cordless garden system — SBS460CLM cordless lawn mower, SBS480CBV cordless blower vacuum, SBS560CHT cordless hedge trimmer, SBS240CPHT cordless pole hedge trimmer (all sold separately).\n\n"
            "Backed by Spectrum's 5-year warranty."
        ),
    },
    "SBSCSC": {
        "title": "SPECTRUM SBSCSC 40V Fast Battery Charger, 2A Single-Bay, 4x Faster Than Standard, Compatible with All Spectrum 40V Lithium-Ion Batteries",
        "bullets": [
            "2A FAST CHARGE: Replenishes a 2.0Ah battery in about an hour, a 4.0Ah pack in roughly two — four times faster than the standard 0.5A model.",
            "SPLIT-SESSION READY: Charge during a lunch break and be back to work in the afternoon. The natural choice if you rotate a single battery between multiple tools.",
            "PROTECTED CHARGING: Built-in charge management circuitry monitors the battery throughout the cycle, cutting off automatically at full capacity to protect cell health and prevent overcharging.",
            "470G, COMPACT BUILD: Small enough to stash in a drawer, shed shelf, or toolbox between uses. Easy to move between charging locations.",
            "FITS ALL SPECTRUM 40V BATTERIES: Works with SBS20CB (2.0Ah) and SBS40CB (4.0Ah). Powers Spectrum 40V cordless tools — SBS460CLM, SBS480CBV, SBS560CHT, SBS240CPHT (sold separately).",
        ],
        "description": (
            "Don't let a flat battery interrupt your day. The Spectrum SBSCSC fast charger delivers a 2A charge at 40V, replenishing your Spectrum lithium-ion battery four times faster than the standard 0.5A model. A 2.0Ah battery can be fully charged in around an hour; a 4.0Ah pack in roughly two.\n\n"
            "Compatible with all Spectrum 40V batteries, the SBSCSC is the natural choice if your garden sessions are split across the day or you're running a single battery between multiple tools. Charge during a lunch break, and you're ready for the afternoon.\n\n"
            "Despite the faster output, the charger remains compact and practical at just 470g. Built-in charge management circuitry monitors the battery throughout the charge cycle, cutting off automatically at full capacity to protect cell health and prevent overcharging.\n\n"
            "Compatible with all Spectrum 40V lithium-ion batteries, sold separately: SBS20CB (2.0Ah) and SBS40CB (4.0Ah).\n\n"
            "Powers the Spectrum 40V cordless garden system — SBS460CLM cordless lawn mower, SBS480CBV cordless blower vacuum, SBS560CHT cordless hedge trimmer, SBS240CPHT cordless pole hedge trimmer (all sold separately).\n\n"
            "For the heaviest users, consider the Spectrum SBSCDC dual-bay charger (sold separately) — charge two batteries simultaneously for continuous runtime across multiple tools.\n\n"
            "Backed by Spectrum's 5-year warranty."
        ),
    },
    "SBSCBC": {
        "title": "SPECTRUM SBSCBC 40V Standard Battery Charger, 0.5A Overnight Charging, Battery-Friendly Slow Charge, Compatible with All Spectrum 40V Lithium-Ion Batteries",
        "bullets": [
            "0.5A OVERNIGHT CHARGING: Plug in after a session, leave it overnight, and your battery is fully replenished and ready by morning.",
            "BATTERY-FRIENDLY SLOW CHARGE: A gentle rate widely regarded as the kindest charge cycle for lithium-ion longevity — ideal if you're looking after a pack for the long term.",
            "LED STATUS INDICATION: Simple LED lets you know when charging is underway and when it's complete. No ambiguity, no guesswork.",
            "UNDER 200G, TOOLBOX-SIZED: Tucks away neatly in a drawer, shed shelf, or toolbox between uses — no wasted storage space.",
            "FITS ALL SPECTRUM 40V BATTERIES: Works with SBS20CB (2.0Ah) and SBS40CB (4.0Ah). Powers Spectrum 40V cordless tools — SBS460CLM, SBS480CBV, SBS560CHT, SBS240CPHT (sold separately).",
        ],
        "description": (
            "The Spectrum SBSCBC is the no-fuss standard charger for all compatible Spectrum 40V lithium-ion batteries. Plug in after a session, leave it overnight, and your battery will be fully replenished and ready to go again by morning.\n\n"
            "With a steady 0.5A output at 40V, the SBSCBC charges at a gentle, battery-friendly rate — ideal if your toolkit runs on a single battery that you want to look after for the long term. Slow overnight charging is widely regarded as the kindest charge cycle for lithium-ion longevity.\n\n"
            "The charger is compact and weighs under 200g, so it tucks away neatly in a drawer, shed shelf, or toolbox. Simple LED indication lets you know when charging is underway and when it's complete.\n\n"
            "Compatible with all Spectrum 40V lithium-ion batteries, sold separately: SBS20CB (2.0Ah) and SBS40CB (4.0Ah).\n\n"
            "Powers the Spectrum 40V cordless garden system — SBS460CLM cordless lawn mower, SBS480CBV cordless blower vacuum, SBS560CHT cordless hedge trimmer, SBS240CPHT cordless pole hedge trimmer (all sold separately).\n\n"
            "If faster charging is a priority, consider upgrading to the Spectrum SBSCSC fast charger (2A single-bay) or SBSCDC dual-bay charger (sold separately) — both cut charge time to a quarter of the standard rate.\n\n"
            "Backed by Spectrum's 5-year warranty."
        ),
    },
}


def get_product_type(client: AmazonClient, sku: str) -> str:
    """Fetch the existing listing's productType so PATCH validates against the right schema."""
    resp = client.get(
        f"/listings/2021-08-01/items/{SELLER_ID}/{sku}",
        params={"marketplaceIds": MARKETPLACE, "includedData": "summaries"},
    )
    summaries = resp.get("summaries") or []
    if not summaries:
        raise SystemExit(f"No summary returned for SKU {sku} — does it exist on this seller account?")
    return summaries[0]["productType"]


def push(client: AmazonClient, sku: str) -> dict:
    listing = LISTINGS[sku]
    print(f"\n=== {sku} ===")
    print(f"  Title: {listing['title'][:80]}…")
    print(f"  Bullets: {len(listing['bullets'])} | Description: {len(listing['description'])} chars")

    product_type = get_product_type(client, sku)
    print(f"  productType: {product_type}")

    patches = [
        {
            "op": "replace",
            "path": "/attributes/item_name",
            "value": [{
                "value": listing["title"],
                "language_tag": LANG,
                "marketplace_id": MARKETPLACE,
            }],
        },
        {
            "op": "replace",
            "path": "/attributes/bullet_point",
            "value": [
                {"value": b, "language_tag": LANG, "marketplace_id": MARKETPLACE}
                for b in listing["bullets"]
            ],
        },
        {
            "op": "replace",
            "path": "/attributes/product_description",
            "value": [{
                "value": listing["description"],
                "language_tag": LANG,
                "marketplace_id": MARKETPLACE,
            }],
        },
    ]

    payload = {"productType": product_type, "patches": patches}

    resp = client.patch(
        f"/listings/2021-08-01/items/{SELLER_ID}/{sku}",
        payload=payload,
        params={"marketplaceIds": MARKETPLACE},
    )

    status        = resp.get("status")
    submission_id = resp.get("submissionId")
    issues        = resp.get("issues") or []
    print(f"  → status={status}  submissionId={submission_id}")
    if issues:
        print(f"  ! {len(issues)} issue(s):")
        for iss in issues:
            sev  = iss.get("severity")
            code = iss.get("code")
            msg  = iss.get("message")
            attr = iss.get("attributeNames") or []
            print(f"      [{sev}] {code}: {msg}  attrs={attr}")
    return resp


def main():
    parser = argparse.ArgumentParser()
    group  = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sku", help="Push a single SKU")
    group.add_argument("--all", action="store_true", help="Push all 9 SKUs")
    group.add_argument("--remaining", action="store_true",
                       help="Push all SKUs except SBS460CLM (already pushed as sanity check)")
    args = parser.parse_args()

    if args.sku:
        skus = [args.sku]
    elif args.remaining:
        skus = [s for s in LISTINGS if s != "SBS460CLM"]
    else:
        skus = list(LISTINGS)

    client  = AmazonClient()
    results = {}
    for sku in skus:
        try:
            results[sku] = push(client, sku)
        except AmazonSPAPIError as e:
            print(f"  ✗ FAILED {sku}: {e}")
            results[sku] = {"error": str(e)}
        time.sleep(1.0)  # gentle pacing — listings PATCH is 5 req/sec but we don't need speed

    print("\n=== Summary ===")
    for sku, r in results.items():
        if "error" in r:
            print(f"  {sku}: ERROR — {r['error'][:120]}")
        else:
            print(f"  {sku}: {r.get('status')}  ({len(r.get('issues') or [])} issues)")


if __name__ == "__main__":
    main()
