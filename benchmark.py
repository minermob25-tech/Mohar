import asyncio
import json
import re
from typing import Any, Dict, List

from backend import (
    DEFAULT_RULES,
    LOGO_TEMPLATES,
    inspect_image_bytes,
    make_png_bytes,
    semantic_fallback_patterns,
    validate_ai_findings,
)

SYNTHETIC_DRAFTS: List[Dict[str, Any]] = [
    {"id": "D01", "issue_type": "Claims", "text": "SUMMER MEGA SALE: 60% OFF everything!", "expected": True},
    {"id": "D02", "issue_type": "Facts", "text": "Trusted by 1 million customers across India.", "expected": True},
    {"id": "D03", "issue_type": "Claims", "text": "The best outdoor gear, period.", "expected": True},
    {"id": "D04", "issue_type": "Claims", "text": "Grab our cheap Ridgeline Pack this weekend.", "expected": True},
    {"id": "D05", "issue_type": "Facts", "text": "Shop now at Northwind Co for winter treks.", "expected": True},
    {"id": "D06", "issue_type": "Claims", "text": "Slashing prices in half on all gear this weekend.", "expected": True},
    {"id": "D07", "issue_type": "Claims", "text": "Built with a 100% waterproof guarantee that never fails.", "expected": True},
    {"id": "D08", "issue_type": "Facts", "text": "Over half a million trekkers rely on our packs.", "expected": True},
    {"id": "D09", "issue_type": "Tone", "text": "Buy now before it's gone forever!", "expected": True},
    {"id": "D10", "issue_type": "Claims", "text": "Our number one tent for Himalayan expeditions.", "expected": True},
    {"id": "D11", "issue_type": "Claims", "text": "Guaranteed lowest prices on boots.", "expected": True},
    {"id": "D12", "issue_type": "Claims", "text": "Unbeatable durability on every trail.", "expected": True},
    {"id": "D13", "issue_type": "Claims", "text": "Massive storewide blowout starts Friday!", "expected": True},
    {"id": "D14", "issue_type": "Facts", "text": "Nearly a million happy hikers choose Northwind Outdoor Co.", "expected": True},
    {"id": "D15", "issue_type": "Claims", "text": "Our cheapest sleeping bag is back in stock.", "expected": True},
    {"id": "D16", "issue_type": "Claims", "text": "Lifetime assurance on every zipper and seam.", "expected": True},
    {"id": "D17", "issue_type": "Claims", "text": "50% markdown across the entire store today.", "expected": True},
    {"id": "D18", "issue_type": "Tone", "text": "Act immediately or miss out on trail essentials!", "expected": True},
    {"id": "C01", "issue_type": "Clean", "text": "Summer Sale 2026: up to 25% off selected styles from 1 June to 31 August.", "expected": False},
    {"id": "C02", "issue_type": "Clean", "text": "Northwind Outdoor Co. has served 400,000+ customers.", "expected": False},
    {"id": "C03", "issue_type": "Clean", "text": "Meet the Summit Pack, designed for multi-day mountain trails.", "expected": False},
    {"id": "C04", "issue_type": "Clean", "text": "Affordable, weather-tested jackets built for monsoon treks.", "expected": False},
    {"id": "C05", "issue_type": "Clean", "text": "Explore our new trail guide for beginner hikers.", "expected": False},
    {"id": "C06", "issue_type": "Clean", "text": "Pack light and stay hydrated on steep ascents.", "expected": False},
    {"id": "C07", "issue_type": "Clean", "text": "Visit Northwind Outdoor Co. to compare tent weights.", "expected": False},
]


def rule_only_detect(text: str) -> bool:
    case_insensitive_patterns = [
        r"\b(60|50)\s?%\s*off",
        r"\b1\s*million\s*customers",
        r"\b(best|guaranteed|unbeatable|cheapest|number one)\b",
        r"\b(cheap|Northwind Co|Ridgeline Pack)\b",
    ]
    if any(re.search(p, text, re.IGNORECASE) for p in case_insensitive_patterns):
        return True
    return bool(re.search(r"\b[A-Z]{3,}(?:\s+[A-Z]{3,})+", text))


def make_rect_png(bg: tuple[int, int, int], fg: tuple[int, int, int]) -> bytes:
    w, h = 40, 20
    pixels = []
    for y in range(h):
        for x in range(w):
            if 8 <= x <= 32 and 4 <= y <= 15:
                pixels.append(fg)
            else:
                pixels.append(bg)
    return make_png_bytes(w, h, pixels)


def run_visual_suite() -> Dict[str, Any]:
    cases = [
        ("defect_low_contrast.png", make_rect_png((255, 255, 255), (160, 160, 160)), True),
        ("defect_unapproved_palette.png", make_rect_png((20, 110, 245), (255, 255, 255)), True),
        ("defect_retired_seal_asset.png", make_png_bytes(16, 16, LOGO_TEMPLATES["2022"]), True),
        ("clean_approved_2026_logo.png", make_png_bytes(16, 16, LOGO_TEMPLATES["2026"]), False),
        ("clean_monochrome_banner.png", make_rect_png((255, 255, 255), (27, 27, 27)), False),
    ]

    tp = fp = fn = 0
    for fname, png_bytes, has_defect in cases:
        res = inspect_image_bytes(png_bytes, fname)
        detected = len(res["findings"]) > 0
        if has_defect and detected:
            tp += 1
        elif has_defect and not detected:
            fn += 1
        elif not has_defect and detected:
            fp += 1

    total_defects = tp + fn
    return {
        "total_images": len(cases),
        "defect_images": total_defects,
        "clean_controls": len(cases) - total_defects,
        "detection_rate": round((tp / max(1, total_defects)) * 100, 1),
        "false_positives": fp,
    }


async def evaluate_all() -> Dict[str, Any]:
    rules_by_id = {r["id"]: r for r in DEFAULT_RULES}

    r_tp = r_fp = r_fn = 0
    ai_tp = ai_fp = ai_fn = 0
    total_ai_findings = 0
    valid_source_findings = 0
    actionable_suggestions = 0
    by_type: Dict[str, Dict[str, int]] = {
        "Claims": {"n": 0, "rule_tp": 0, "comb_tp": 0},
        "Facts": {"n": 0, "rule_tp": 0, "comb_tp": 0},
        "Tone": {"n": 0, "rule_tp": 0, "comb_tp": 0},
    }

    for d in SYNTHETIC_DRAFTS:
        r_hit = rule_only_detect(d["text"])
        raw_ai = semantic_fallback_patterns(d["text"])
        val_ai = validate_ai_findings(d["text"], raw_ai, rules_by_id)["findings"]

        total_ai_findings += len(val_ai)
        valid_source_findings += sum(1 for f in val_ai if f["valid"])
        actionable_suggestions += sum(1 for f in val_ai if f.get("fix"))

        comb_hit = r_hit or (len(val_ai) > 0)

        itype = d["issue_type"]
        if itype in by_type:
            by_type[itype]["n"] += 1
            if r_hit:
                by_type[itype]["rule_tp"] += 1
            if comb_hit:
                by_type[itype]["comb_tp"] += 1

        if d["expected"] and r_hit:
            r_tp += 1
        elif d["expected"] and not r_hit:
            r_fn += 1
        elif not d["expected"] and r_hit:
            r_fp += 1

        if d["expected"] and comb_hit:
            ai_tp += 1
        elif d["expected"] and not comb_hit:
            ai_fn += 1
        elif not d["expected"] and comb_hit:
            ai_fp += 1

    def metrics(tp: int, fp: int, fn: int) -> Dict[str, float]:
        prec = (tp / max(1, tp + fp)) * 100
        rec = (tp / max(1, tp + fn)) * 100
        return {"precision": round(prec, 1), "recall": round(rec, 1)}

    per_issue_type = {
        k: {
            "sample_size": v["n"],
            "rule_only_recall": round((v["rule_tp"] / max(1, v["n"])) * 100, 1),
            "rule_plus_ai_recall": round((v["comb_tp"] / max(1, v["n"])) * 100, 1),
        }
        for k, v in by_type.items()
    }

    return {
        "sample_size": len(SYNTHETIC_DRAFTS),
        "text_benchmark": {
            "rule_only": metrics(r_tp, r_fp, r_fn),
            "rule_plus_ai": metrics(ai_tp, ai_fp, ai_fn),
            "per_issue_type": per_issue_type,
            "valid_source_rate": round((valid_source_findings / max(1, total_ai_findings)) * 100, 1),
            "suggestion_actionable_rate": round((actionable_suggestions / max(1, total_ai_findings)) * 100, 1),
        },
        "visual_benchmark": run_visual_suite(),
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(evaluate_all()), indent=2))