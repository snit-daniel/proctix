"""Evaluate local, consented face pairs. Does not install an automatic approval threshold.

CSV: reference,candidate,same_person,group
Paths are relative to the manifest. same_person is 0 or 1; group is an optional anonymous cohort.
"""

import argparse
import csv
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from proctix.vision import FaceEngine, canonical_image


def interval(errors, trials):
    if not trials:
        return None
    p, z = errors / trials, 1.96
    center = (p + z * z / (2 * trials)) / (1 + z * z / trials)
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / (1 + z * z / trials)
    return {
        "errors": errors,
        "trials": trials,
        "rate": p,
        "wilson_95": [max(0, center - half), min(1, center + half)],
    }


def summarize(rows, threshold):
    same = [r for r in rows if r["same_person"]]
    different = [r for r in rows if not r["same_person"]]

    def comparable(rs):
        return [r for r in rs if r["score"] is not None]

    positive, negative = comparable(same), comparable(different)
    return {
        "total_pairs": len(rows),
        "quality_or_processing_rejections": sum(r["score"] is None for r in rows),
        "false_match": interval(sum(r["score"] >= threshold for r in negative), len(negative)),
        "false_nonmatch": interval(sum(r["score"] < threshold for r in positive), len(positive)),
        "same_person_unassessable": len(same) - len(positive),
        "different_person_unassessable": len(different) - len(negative),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--models", type=Path, default=Path("models"))
    parser.add_argument("--threshold", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not -1 <= args.threshold <= 1:
        parser.error("Cosine threshold must lie in [-1, 1]")
    engine = FaceEngine(args.models)
    if not engine.available:
        raise RuntimeError("Model weights unavailable or corrupt")
    rows = []
    with args.manifest.open(newline="") as file:
        for pair in csv.DictReader(file):
            if pair["same_person"] not in ("0", "1"):
                raise ValueError("same_person must be 0 or 1")
            images = []
            for key in ("reference", "candidate"):
                path = (args.manifest.parent / pair[key]).resolve()
                if path.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError("Evaluation image too large")
                images.append(canonical_image(path.read_bytes()))
            result = engine.compare(*images)
            rows.append(
                {
                    "same_person": pair["same_person"] == "1",
                    "group": pair.get("group") or "all",
                    "score": result.get("cosine_similarity"),
                }
            )
    result = {
        "model": "AuraFace-v1-ResNet100",
        "threshold_evaluated": args.threshold,
        "deployment_approved": False,
        "automatic_approval_enabled": False,
        "overall": summarize(rows, args.threshold),
        "groups": {
            group: summarize([r for r in rows if r["group"] == group], args.threshold)
            for group in sorted({r["group"] for r in rows})
        },
        "notes": [
            "Use independent consented holdout subjects and representative cameras/lighting/skin tones/ages.",
            "Wilson intervals assume independent trials; repeated subjects may invalidate that assumption.",
            "Accuracy on these pairs does not establish document authenticity, liveness or resistance to replay.",
        ],
    }
    args.output.write_text(json.dumps(result, indent=2))
    print("Evaluation report written; automatic approval remains disabled.")


if __name__ == "__main__":
    main()
