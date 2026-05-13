"""
Create the 5 SPECTRUM kit ASINs on Amazon UK via SP-API putListingsItem.

Source of truth: Shopify kit products (post-migration 2026-04-25), GS1-issued EANs
(allocated 2026-04-26), and the approved drafts in
reports/amazon_kit_listing_drafts.md.

Templates:
  - LAWN_MOWER                  (SBS460CLM standalone)
  - LEAF_BLOWER                 (SBS480CBV standalone)
  - RECIPROCATING_HEDGE_TRIMMER (SBS560CHT standalone)

Usage:
    python3.11 scripts/amazon_kits_push.py --sku SBS220CHM-KIT
    python3.11 scripts/amazon_kits_push.py --all
    python3.11 scripts/amazon_kits_push.py --dry-run --sku SBS460CLM-KIT
"""
import os
import sys
import time
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from amazon_client import AmazonClient, AmazonSPAPIError

LANG        = "en_GB"
MARKETPLACE = "A1F83G8C2ARO7P"
SELLER_ID   = os.environ["AMAZON_SELLER_ID"]


# Browse nodes per productType (UK), pulled from existing standalone listings
BROWSE_NODE = {
    "LAWN_MOWER":                  "900813031",
    "LEAF_BLOWER":                 "114627031",
    "RECIPROCATING_HEDGE_TRIMMER": "114631031",
}


KITS = {
    "SBS460CLM-KIT": {
        "productType": "LAWN_MOWER",
        "ean":         "5061122340134",
        "title": (
            "SPECTRUM SBS460CLM 40V Cordless Self-Propelled Lawn Mower, 46cm Cut, "
            "Brushless Motor, 7 Cutting Heights (25–75mm), 60L Bag, Mulch — "
            "Complete Kit (2× 4.0Ah Batteries + Dual Fast Charger Included)"
        ),
        "bullets": [
            "COMPLETE READY-TO-MOW KIT: Includes the SBS460CLM mower plus 2× SBS40CB 40V 4.0Ah lithium-ion batteries and 1× SBSCDC dual-bay fast charger — no extras to buy.",
            "SELF-PROPELLED DRIVE: Transmission does the walking for you — ideal for medium-to-large lawns and uneven ground where a push-along becomes hard work.",
            "46CM BRUSHLESS CUTTING DECK: High-efficiency brushless motor runs a 46cm (18\") steel deck at consistent speed — handles thick grass without bogging down or losing pace.",
            "SEVEN HEIGHTS, 4-IN-1 DISPOSAL: 25mm to 75mm in seven steps via single central lever; rear-collect into 60L bag (bag-full indicator), mulch, rear-discharge, or side-discharge.",
            "CONTINUOUS RUNTIME: 2× 4.0Ah batteries for extended sessions; rotate through the SBSCDC dual charger (2A per port) for unbroken work. 5-year warranty.",
        ],
        "description": (
            "The Spectrum SBS460CLM Complete Kit is everything you need to mow medium-to-large gardens straight out of the box — mower, two 40V 4.0Ah batteries, and a dual-bay fast charger.\n\n"
            "The SBS460CLM is a cordless self-propelled lawn mower built for medium-to-large gardens that demand more than a basic push-along. A high-efficiency brushless motor, powered by a dual 40V battery configuration, keeps cutting speed consistent from the first strip to the last — even in thicker grass.\n\n"
            "The 46cm (18\") steel cutting deck covers ground quickly without compromising finish. Seven cutting heights from 25mm to 75mm adjust in seconds via a single central lever — scalp-close in early spring, a lush longer cut through summer heat.\n\n"
            "Grass disposal, four ways: rear collection into the 60-litre bag (with a bag-full indicator so there's no guesswork), mulching back into the lawn, rear discharge, or side discharge. Large ball-bearing wheels — 7\" front, 10\" rear — roll smoothly over uneven ground and resist turf damage.\n\n"
            "This kit pairs the mower with 2× SBS40CB 40V 4.0Ah lithium-ion batteries (high-grade 21700 cells, steady voltage through full discharge) and 1× SBSCDC dual-bay fast charger — both ports at 2A, charges a 4.0Ah pack in roughly two hours. Rotate batteries through the charger for continuous work.\n\n"
            "Part of the Spectrum 40V cordless garden system — share batteries across our hedge trimmers, pole trimmers, and leaf blower vacs. Backed by Spectrum's 5-year warranty."
        ),
        "list_price":         579.95,
        "our_price":          399.95,
        "inventory":          99,
        "main_image":         "https://cdn.shopify.com/s/files/1/0654/0004/5735/files/SBS460CLM_4488b7a9-01c4-49a9-b9bd-36aa29c2ed65.webp?v=1777187538",
        "other_image":        "https://cdn.shopify.com/s/files/1/0654/0004/5735/files/Spectrum-SBS460CLM-Lawnmower-scaled-e1773826074832_6db6bd86-de05-4660-8c1d-4da391888611.webp?v=1777187538",
        "color":              "Green, Black",
        "item_weight_kg":     28.3,
        "package_weight_kg":  32.0,
        "dimensions_cm":      (84.0, 52.0, 50.0),  # length, width, height
        "num_batteries":      2,  # 2× SBS40CB
        "battery_sku":        "SBS40CB",
    },
    "SBS220CHM-KIT": {
        "productType": "LAWN_MOWER",
        "ean":         "5061122340141",
        "title": (
            "SPECTRUM SBS220CHM 40V Cordless Handy Mower, 22cm Cut, Brushless Motor, "
            "3 Cutting Heights (30–50mm), Mulching, 3.8kg Lightweight — "
            "Complete Kit (2.0Ah Battery + Charger Included)"
        ),
        "bullets": [
            "COMPLETE READY-TO-MOW KIT: Includes the SBS220CHM handy mower plus 1× SBS20CB 40V 2.0Ah lithium-ion battery and 1× SBSCBC standard charger — ready to use out of the box.",
            "ULTRA-LIGHTWEIGHT 3.8KG: One of the lightest cordless mowers available — easy to carry between gardens, lift over steps, and store vertically in a shed or cupboard.",
            "22CM COMPACT CUTTING DECK: Reaches awkward corners, tight edges, and small lawns where a full-size mower can't fit. The mower that goes where others can't.",
            "BRUSHLESS MOTOR AT 6,000RPM: Higher efficiency, longer life, and more runtime per charge than brush motors. Cuts cleanly and consistently across the height range.",
            "THREE HEIGHTS + MULCH: 30mm to 50mm in three steps; built-in mulching feeds chopped clippings back into the lawn. Folding handle for compact storage. 5-year warranty.",
        ],
        "description": (
            "The Spectrum SBS220CHM Complete Kit is the small-lawn solution in one box — handy mower, 40V 2.0Ah battery, and standard charger.\n\n"
            "The SBS220CHM is the mower that goes where others can't. Small lawns, awkward corners, tight edges around borders and obstacles — the SBS220CHM is built for exactly these situations. At just 3.8kg, it's one of the lightest cordless mowers you'll find.\n\n"
            "Despite its compact dimensions, the SBS220CHM doesn't compromise on quality. A high-efficiency brushless motor provides reliable performance, longer battery life per charge, and less ongoing maintenance than a brushed equivalent. Spinning at 6,000rpm, it cuts cleanly and consistently across the cutting height range.\n\n"
            "Three central height settings from 30mm to 50mm let you adapt to the season and your lawn's condition. The built-in mulching function returns finely chopped clippings directly to the lawn — feeding the soil and eliminating the need to collect and dispose of grass waste.\n\n"
            "The flexible handle folds down for compact storage, and the balanced weight distribution makes it easy to lift over steps, carry between areas, or store vertically in a shed or cupboard.\n\n"
            "This kit includes 1× SBS20CB 40V 2.0Ah lithium-ion battery (high-quality 18650 cells, 710g pocket-sized pack) and 1× SBSCBC standard charger (gentle 0.5A overnight charge — kindest for long-term battery health).\n\n"
            "Part of the Spectrum 40V cordless garden system — share batteries across our larger lawn mowers, hedge trimmers, pole trimmers, and leaf blower vacs. Backed by Spectrum's 5-year warranty."
        ),
        "list_price":         289.95,
        "our_price":          149.95,
        "inventory":          190,
        "main_image":         "https://cdn.shopify.com/s/files/1/0654/0004/5735/files/Spectrum-SBS220CHM-Handy-Mower-Main_674e63f2-f328-4486-91d9-00fa04e45f6c.webp?v=1777187556",
        "other_image":        "https://cdn.shopify.com/s/files/1/0654/0004/5735/files/Spectrum-SBS220CHM-Handy-Mower-Left_ea104ebe-76b9-4a82-84db-c97f598f2d7c.webp?v=1777187556",
        "color":              "Green, Black",
        "item_weight_kg":     4.7,
        "package_weight_kg":  6.0,
        "dimensions_cm":      (50.0, 35.0, 25.0),
        "num_batteries":      1,
        "battery_sku":        "SBS20CB",
    },
    "SBS480CBV-KIT": {
        "productType": "LEAF_BLOWER",
        "ean":         "5061122340158",
        "title": (
            "SPECTRUM SBS480CBV 40V Cordless Leaf Blower Vacuum, 3-in-1 Blow/Vac/Mulch, "
            "52m/s Airspeed, 12L Bag, 30:1 Mulch Ratio — "
            "Complete Kit (4.0Ah Battery + Charger Included)"
        ),
        "bullets": [
            "COMPLETE READY-TO-USE KIT: Includes the SBS480CBV blower vacuum plus 1× SBS40CB 40V 4.0Ah lithium-ion battery and 1× SBSCBC standard charger — no extras to buy.",
            "3-IN-1 BLOW, VAC, MULCH: One tool for autumn leaves, post-mow clippings, and damp debris — no cables, no compromise, no exhaust fumes.",
            "52M/S AIR SPEED: Blower mode delivers 52m/s at 264m³/h — shifts stubborn wet leaves from paths, patios, and lawn edges in quick passes.",
            "30:1 MULCH RATIO: Vacuum mode jumps to 588m³/h, pulling debris into the 12-litre bag while a 3-blade mulcher shreds it to a fraction of its volume — fewer compost trips.",
            "4.26KG COMFORT, 5-YEAR WARRANTY: Lightweight build with adjustable handle for fatigue-free sessions. Reliable brush motor gives consistent airflow through the charge.",
        ],
        "description": (
            "The Spectrum SBS480CBV Complete Kit handles autumn leaves and post-mow tidy-ups in one box — blower vacuum, 40V 4.0Ah battery, and standard charger.\n\n"
            "The SBS480CBV is a 40V cordless blower vacuum that handles three jobs in one tool: blow, vacuum, and mulch. From autumn leaves and post-mow clippings to damp debris in hard-to-reach corners, no cables means no compromise on where you can work.\n\n"
            "In blower mode, the SBS480CBV generates an air speed of up to 52m/s with an air volume of 264m³/h — enough to shift stubborn wet leaves from paths, patios, and lawn edges in quick, efficient passes. Switch to vacuum mode and the air volume jumps to 588m³/h, pulling debris into the 12-litre collection bag while the built-in 3-blade mulching system shreds it at a 30:1 ratio. A full bag of leaves compresses down to a fraction of its original volume — fewer trips to the compost heap, more time getting on with the job.\n\n"
            "Weighing just 4.26kg with an adjustable handle, the SBS480CBV stays comfortable during longer sessions. A reliable brush motor delivers consistent airflow without the noise and exhaust of petrol alternatives.\n\n"
            "This kit includes 1× SBS40CB 40V 4.0Ah lithium-ion battery (high-grade 21700 cells, steady voltage through full discharge — the right pack for higher-draw tools like blower vacs) and 1× SBSCBC standard charger (gentle 0.5A overnight charge).\n\n"
            "Part of the Spectrum 40V cordless garden system — share batteries across our lawn mowers, hedge trimmers, and pole trimmers. Backed by Spectrum's 5-year warranty."
        ),
        "list_price":         289.95,
        "our_price":          139.95,
        "inventory":          190,
        "main_image":         "https://cdn.shopify.com/s/files/1/0654/0004/5735/files/Spectrum-SBS480CBV-Blower-Vac_9a50d813-e729-49d9-9466-95fe11fb8a36.png?v=1777187568",
        "other_image":        "https://cdn.shopify.com/s/files/1/0654/0004/5735/files/Spectrum-SBS480CBV-Blower-Vac-Left_99950245-2693-4e97-a9ba-0054f76d9399.png?v=1777187568",
        "color":              "Green, Black",
        "item_weight_kg":     5.4,
        "package_weight_kg":  7.0,
        "dimensions_cm":      (90.0, 25.0, 30.0),
        "num_batteries":      1,
        "battery_sku":        "SBS40CB",
    },
    "SBS560CHT-KIT": {
        "productType": "RECIPROCATING_HEDGE_TRIMMER",
        "ean":         "5061122340165",
        "title": (
            "SPECTRUM SBS560CHT 40V Cordless Hedge Trimmer, 45cm Laser-Cut Blade, "
            "Brushless Motor, 2,600 Cuts/Min, 18mm Capacity, 90° Rotating Handle — "
            "Complete Kit (2.0Ah Battery + Charger Included)"
        ),
        "bullets": [
            "COMPLETE READY-TO-TRIM KIT: Includes the SBS560CHT hedge trimmer plus 1× SBS20CB 40V 2.0Ah lithium-ion battery and 1× SBSCBC standard charger — ready to use out of the box.",
            "BRUSHLESS MOTOR: Runs cooler, lasts longer, and extracts more runtime per charge than brush-motor trimmers. Keeps cutting speed steady through the battery.",
            "45CM LASER-CUT BLADE: 2,600 cuts per minute moves through established hedges quickly — sessions stay short and results stay consistent.",
            "18MM CUTTING CAPACITY: Handles the thicker, woodier growth in mature hedges without stalling or snagging — no need to drop down to loppers for branch-level stems.",
            "90° ROTATING HANDLE, 2.91KG: Switch between horizontal top cuts and vertical face cuts without adjusting your stance — reduces wrist and shoulder strain on longer sessions. 5-year warranty.",
        ],
        "description": (
            "The Spectrum SBS560CHT Complete Kit is everything you need for neat hedges in one box — hedge trimmer, 40V 2.0Ah battery, and standard charger.\n\n"
            "Neat hedges don't have to mean hard work. The SBS560CHT cordless hedge trimmer combines a powerful brushless motor with a precision laser-cut blade for clean, professional-looking results with minimal fatigue.\n\n"
            "The brushless motor is a meaningful upgrade over brush-motor alternatives — it runs more efficiently, generates less heat, lasts longer between services, and extracts more runtime from each battery charge. At 2,600 cuts per minute, the 45cm cutting blade moves through foliage quickly, keeping trimming sessions short and results consistent.\n\n"
            "With an 18mm cutting capacity, the SBS560CHT handles the thicker, woodier growth found in established hedges without stalling or snagging. The 90° rotating handle lets you switch between horizontal top cuts and vertical face cuts without adjusting your stance or grip — reducing strain on wrists and shoulders during longer sessions.\n\n"
            "At just 2.91kg, it's light enough to hold overhead comfortably for as long as the job demands.\n\n"
            "This kit includes 1× SBS20CB 40V 2.0Ah lithium-ion battery (high-quality 18650 cells, 710g pocket-sized pack — the natural fit for lighter cordless tools) and 1× SBSCBC standard charger (gentle 0.5A overnight charge).\n\n"
            "Part of the Spectrum 40V cordless garden system — share batteries across our lawn mowers, pole trimmers, and leaf blower vacs. Backed by Spectrum's 5-year warranty."
        ),
        "list_price":         239.95,
        "our_price":          139.95,
        "inventory":          155,
        "main_image":         "https://cdn.shopify.com/s/files/1/0654/0004/5735/files/Spectrum-SBS560CHT-Cordless-Hedge-Trimmer-Main_a5c36637-6ced-43ff-9370-57c00e5ea470.webp?v=1777187551",
        "other_image":        "https://cdn.shopify.com/s/files/1/0654/0004/5735/files/Spectrum-SBS560CHT-Cordless-Hedge-Trimmer-Left_4063a005-5bdf-4e0c-b5d5-09fe4facd7f9.webp?v=1777187552",
        "color":              "Green, Black",
        "item_weight_kg":     3.8,
        "package_weight_kg":  5.0,
        "dimensions_cm":      (95.0, 25.0, 25.0),
        "num_batteries":      1,
        "battery_sku":        "SBS20CB",
    },
    "SBS240CPHT-KIT": {
        "productType": "RECIPROCATING_HEDGE_TRIMMER",
        "ean":         "5061122340172",
        "title": (
            "SPECTRUM SBS240CPHT 40V Cordless Pole Hedge Trimmer, 2.4m Reach, "
            "46cm Dual-Action Laser-Cut Blade, 180° Adjustable Head, 18mm Capacity — "
            "Complete Kit (4.0Ah Battery + Charger Included)"
        ),
        "bullets": [
            "COMPLETE READY-TO-TRIM KIT: Includes the SBS240CPHT pole hedge trimmer plus 1× SBS40CB 40V 4.0Ah lithium-ion battery and 1× SBSCBC standard charger — ready out of the box.",
            "2.4M GROUND REACH: Handles tall hedges and high-sided borders safely from the ground — no ladder, no compromised balance.",
            "180° ADJUSTABLE HEAD: Trim horizontally along the top of a hedge or vertically down the face without awkward repositioning — get the angle ladders can't reach.",
            "46CM DUAL-ACTION BLADE: Laser-cut blade runs at 2,600 strokes per minute, slicing through branches up to 18mm thick with clean cuts that promote healthy regrowth.",
            "3.62KG LIGHTWEIGHT, 5-YEAR WARRANTY: Well-balanced at extension, reducing shoulder and arm fatigue on longer hedgerows. Reliable brush motor holds cutting speed through the charge.",
        ],
        "description": (
            "The Spectrum SBS240CPHT Complete Kit is everything you need to tackle tall hedges from the ground — pole trimmer, 40V 4.0Ah battery, and standard charger.\n\n"
            "Tall hedges and high-sided borders are no match for the SBS240CPHT cordless pole hedge trimmer. With a total reach of 2.4 metres and a 180° adjustable cutting head, it handles the angles that ladders and standard trimmers can't — safely, from the ground.\n\n"
            "The 46cm dual-action, laser-cut blade runs at 2,600 strokes per minute, slicing through branches up to 18mm thick with clean, precise cuts that promote healthy regrowth. Whether you're trimming horizontally along the top of a hedge or working vertically down the face, the rotating head gives you the position you need without awkward repositioning.\n\n"
            "Power comes from Spectrum's 40V lithium-ion battery system. The reliable brush motor maintains consistent cutting speed throughout the charge, and at just 3.62kg the tool stays well-balanced and fatigue-free even on longer hedgerows.\n\n"
            "This kit includes 1× SBS40CB 40V 4.0Ah lithium-ion battery (high-grade 21700 cells, steady voltage through full discharge — the right pack for sustained pole trimmer sessions) and 1× SBSCBC standard charger (gentle 0.5A overnight charge).\n\n"
            "Part of the Spectrum 40V cordless garden system — share batteries across our lawn mowers, hedge trimmers, and leaf blower vacs. Backed by Spectrum's 5-year warranty."
        ),
        "list_price":         309.95,
        "our_price":          149.95,
        "inventory":          139,
        "main_image":         "https://cdn.shopify.com/s/files/1/0654/0004/5735/files/Spectrum-SBS240CPHT-Pole-Hedge-Trimmer-Main_8d207719-c55d-4bb4-8d58-895e89eb4db6.webp?v=1777187561",
        "other_image":        None,
        "color":              "Green, Black",
        "item_weight_kg":     4.7,
        "package_weight_kg":  6.0,
        "dimensions_cm":      (250.0, 25.0, 25.0),
        "num_batteries":      1,
        "battery_sku":        "SBS40CB",
    },
}


BATTERY_SPECS = {
    "SBS20CB": {"capacity_ah": 2.0, "weight_kg": 0.75, "energy_wh": 80.0,  "cells": 10},
    "SBS40CB": {"capacity_ah": 4.0, "weight_kg": 1.0,  "energy_wh": 160.0, "cells": 10},
}


def build_attributes(sku, k):
    ptype = k["productType"]
    L, W, H = k["dimensions_cm"]

    # LAWN_MOWER uses item_depth_width_height; LEAF_BLOWER and HEDGE_TRIMMER use item_length_width_height
    if ptype == "LAWN_MOWER":
        item_dims_key = "item_depth_width_height"
        item_dims_val = {
            "depth":          {"unit": "centimeters", "value": L},
            "width":          {"unit": "centimeters", "value": W},
            "height":         {"unit": "centimeters", "value": H},
            "marketplace_id": MARKETPLACE,
        }
    else:
        item_dims_key = "item_length_width_height"
        item_dims_val = {
            "length":         {"unit": "centimeters", "value": L},
            "width":          {"unit": "centimeters", "value": W},
            "height":         {"unit": "centimeters", "value": H},
            "marketplace_id": MARKETPLACE,
        }

    attrs = {
        "brand":        [{"language_tag": LANG, "value": "SPECTRUM",  "marketplace_id": MARKETPLACE}],
        "manufacturer": [{"language_tag": LANG, "value": "SPECTRUM",  "marketplace_id": MARKETPLACE}],
        "model_name":   [{"language_tag": LANG, "value": sku,         "marketplace_id": MARKETPLACE}],
        "model_number": [{"value": sku, "marketplace_id": MARKETPLACE}],
        "part_number":  [{"value": sku, "marketplace_id": MARKETPLACE}],

        "item_name":           [{"language_tag": LANG, "value": k["title"],       "marketplace_id": MARKETPLACE}],
        "product_description": [{"language_tag": LANG, "value": k["description"], "marketplace_id": MARKETPLACE}],
        "bullet_point": [
            {"language_tag": LANG, "value": b, "marketplace_id": MARKETPLACE}
            for b in k["bullets"]
        ],

        "externally_assigned_product_identifier": [
            {"value": k["ean"], "type": "ean", "marketplace_id": MARKETPLACE}
        ],

        "condition_type":     [{"value": "new_new",   "marketplace_id": MARKETPLACE}],
        "country_of_origin":  [{"value": "CN",        "marketplace_id": MARKETPLACE}],
        "color":              [{"language_tag": LANG, "value": k["color"], "marketplace_id": MARKETPLACE}],

        "power_source_type": [{"language_tag": LANG, "value": "Battery Powered", "marketplace_id": MARKETPLACE}],

        "batteries_included": [{"value": True,  "marketplace_id": MARKETPLACE}],
        "batteries_required": [{"value": True,  "marketplace_id": MARKETPLACE}],
        "supplier_declared_dg_hz_regulation": [{"value": "not_applicable", "marketplace_id": MARKETPLACE}],

        # Required when batteries_included = true (lithium-ion DG compliance, UN3481)
        "battery": [{
            "cell_composition": [{"value": "lithium_ion"}],
            "capacity":         [{"unit": "amp_hours", "value": BATTERY_SPECS[k["battery_sku"]]["capacity_ah"]}],
            "weight":           [{"unit": "kilograms", "value": BATTERY_SPECS[k["battery_sku"]]["weight_kg"]}],
            "marketplace_id":   MARKETPLACE,
        }],
        "lithium_battery": [{
            "energy_content": [{"unit": "watt_hours", "value": BATTERY_SPECS[k["battery_sku"]]["energy_wh"]}],
            "packaging":      [{"value": "batteries_packed_with_equipment"}],
            "marketplace_id": MARKETPLACE,
        }],
        "number_of_lithium_ion_cells": [{
            "value":          BATTERY_SPECS[k["battery_sku"]]["cells"],
            "marketplace_id": MARKETPLACE,
        }],
        "contains_battery_or_cell": [{"value": "battery", "marketplace_id": MARKETPLACE}],
        "has_less_than_30_percent_state_of_charge": [{"value": True, "marketplace_id": MARKETPLACE}],
        "num_batteries": [{
            "quantity":       k["num_batteries"],
            "type":           "nonstandard_battery",
            "marketplace_id": MARKETPLACE,
        }],
        "has_multiple_battery_powered_components": [{"value": False, "marketplace_id": MARKETPLACE}],

        "number_of_items": [{"value": 1, "marketplace_id": MARKETPLACE}],
        "unit_count":      [{"value": 1.0, "marketplace_id": MARKETPLACE}],

        "item_package_weight": [{"unit": "kilograms", "value": k["package_weight_kg"], "marketplace_id": MARKETPLACE}],
        item_dims_key:         [item_dims_val],
        "item_package_dimensions": [{
            "length":         {"unit": "centimeters", "value": L},
            "width":          {"unit": "centimeters", "value": W},
            "height":         {"unit": "centimeters", "value": H},
            "marketplace_id": MARKETPLACE,
        }],

        "main_product_image_locator": [{"media_location": k["main_image"], "marketplace_id": MARKETPLACE}],

        "recommended_browse_nodes": [{"value": BROWSE_NODE[ptype], "marketplace_id": MARKETPLACE}],

        "list_price": [{"currency": "GBP", "value_with_tax": k["list_price"], "marketplace_id": MARKETPLACE}],
        "purchasable_offer": [{
            "currency":       "GBP",
            "audience":       "ALL",
            "marketplace_id": MARKETPLACE,
            "our_price": [{
                "schedule": [{"value_with_tax": k["our_price"]}],
            }],
        }],
        "skip_offer": [{"value": False, "marketplace_id": MARKETPLACE}],

        "fulfillment_availability": [{
            "fulfillment_channel_code": "DEFAULT",
            "quantity": k["inventory"],
        }],
    }

    # Per-productType extras
    if ptype == "LAWN_MOWER":
        attrs["item_weight"]              = [{"unit": "kilograms", "value": k["item_weight_kg"], "marketplace_id": MARKETPLACE}]
        attrs["operation_mode"]           = [{"language_tag": LANG, "value": "Cordless",   "marketplace_id": MARKETPLACE}]
        attrs["power_plug_type"]          = [{"value": "no_plug",  "marketplace_id": MARKETPLACE}]
        attrs["is_assembly_required"]     = [{"value": True,       "marketplace_id": MARKETPLACE}]
        attrs["is_fragile"]               = [{"value": True,       "marketplace_id": MARKETPLACE}]
        attrs["number_of_boxes"]          = [{"value": 1,          "marketplace_id": MARKETPLACE}]
        attrs["merchant_shipping_group"]  = [{"value": "legacy-template-id", "marketplace_id": MARKETPLACE}]

    elif ptype == "RECIPROCATING_HEDGE_TRIMMER":
        attrs["item_weight"]      = [{"unit": "kilograms", "value": k["item_weight_kg"], "marketplace_id": MARKETPLACE}]
        attrs["number_of_boxes"]  = [{"value": 1, "marketplace_id": MARKETPLACE}]

    elif ptype == "LEAF_BLOWER":
        attrs["power_plug_type"]      = [{"value": "no_plug", "marketplace_id": MARKETPLACE}]
        attrs["is_assembly_required"] = [{"value": False,     "marketplace_id": MARKETPLACE}]
        attrs["is_fragile"]           = [{"value": False,     "marketplace_id": MARKETPLACE}]

    # Optional second image
    if k.get("other_image"):
        attrs["other_product_image_locator_1"] = [
            {"media_location": k["other_image"], "marketplace_id": MARKETPLACE}
        ]

    return attrs


def push(client, sku, k, dry_run=False):
    print(f"\n=== {sku} ({k['productType']}) ===")
    print(f"  EAN:      {k['ean']}")
    print(f"  title:    {k['title'][:80]}...")
    print(f"  pricing:  list=£{k['list_price']}  ours=£{k['our_price']}")
    print(f"  stock:    {k['inventory']}")

    payload = {
        "productType":  k["productType"],
        "requirements": "LISTING",
        "attributes":   build_attributes(sku, k),
    }
    print(f"  attributes: {len(payload['attributes'])}")

    if dry_run:
        print("  [DRY RUN] payload not sent")
        return {"status": "DRY_RUN"}

    resp = client.put(
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
            msg  = iss.get("message") or ""
            attr = iss.get("attributeNames") or []
            print(f"      [{sev}] {code}: {msg[:140]}  attrs={attr}")
    else:
        print("  ok: no issues")
    return resp


def main():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--sku", help="Push a single kit SKU")
    g.add_argument("--all", action="store_true", help="Push all 5 kit SKUs")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if args.sku:
        if args.sku not in KITS:
            raise SystemExit(f"Unknown kit SKU: {args.sku}. Known: {list(KITS)}")
        skus = [args.sku]
    else:
        skus = list(KITS)

    client  = AmazonClient() if not args.dry_run else None
    results = {}
    for sku in skus:
        try:
            results[sku] = push(client, sku, KITS[sku], dry_run=args.dry_run)
        except AmazonSPAPIError as e:
            print(f"  ✗ FAILED {sku}: {str(e)[:200]}")
            results[sku] = {"error": str(e)}
        time.sleep(1.0)

    print("\n=== Summary ===")
    for sku, r in results.items():
        if "error" in r:
            print(f"  {sku}: ERROR — {r['error'][:120]}")
        else:
            print(f"  {sku}: {r.get('status')}  ({len(r.get('issues') or [])} issues)")


if __name__ == "__main__":
    main()
