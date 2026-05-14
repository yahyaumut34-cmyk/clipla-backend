#!/usr/bin/env python3
"""
test_shorts.py — Integration + unit tests for the Shorts system.

Usage:
  # Unit test scorer only (no backend, no video):
  python test_shorts.py --unit

  # Full pipeline test (backend must be running):
  python test_shorts.py --job_id YOUR_JOB_ID

  # Score-only preview (no rendering):
  python test_shorts.py --job_id YOUR_JOB_ID --plan-only
"""

import argparse
import json
import sys
import requests

API_BASE = "http://localhost:8000"


# ── Unit test: scorer ──────────────────────────────────────────

def test_scorer_unit():
    from core.shorts import score_segment, build_candidates, generate_shorts_plan, score_all_segments

    print("── Unit: segment scoring ───────────────────────────")

    segs = [
        {"start": 0.0,  "end": 4.0,  "text": "Merhaba arkadaşlar, kanalıma hoş geldiniz."},
        {"start": 4.0,  "end": 9.0,  "text": "Biliyor musun? Çoğu insan bu hatayı yapıyor."},
        {"start": 9.0,  "end": 14.0, "text": "En büyük hata şu ki, kimse bunu bilmiyor."},
        {"start": 14.0, "end": 20.0, "text": "Şok olacaksın ama gerçek şu ki..."},
        {"start": 20.0, "end": 26.0, "text": "Bu yöntemi kullananlar %80 daha hızlı sonuç aldı."},
        {"start": 26.0, "end": 32.0, "text": "Bak şimdi, sana net söyleyeyim: dikkat et buna."},
        {"start": 32.0, "end": 38.0, "text": "Sonuç olarak, bu yüzden bu konuyu bilmen gerekiyor."},
        {"start": 38.0, "end": 44.0, "text": "Özetle kısacası, işte mesele bu."},
        {"start": 44.0, "end": 50.0, "text": "Beğenmeyi ve abone olmayı unutmayın!"},
    ]

    scored = score_all_segments(segs)
    for s in scored:
        print(f"  [{s.index}] score={s.base_score:.2f} hook={s.hook_score:.2f} "
              f"penalty={s.penalty:.2f}  '{s.text[:50]}'")

    assert scored[0].penalty > 0, "Greeting should have penalty"
    assert scored[1].hook_score > 0, "Hook phrase should score"
    assert scored[-1].penalty > 0, "Subscribe CTA should have penalty"

    print("\n── Unit: candidate building ─────────────────────────")
    clips = generate_shorts_plan(segs, total_duration=50.0)
    print(f"  Selected {len(clips)} clips:")
    for c in clips:
        print(f"    {c.start:.1f}s – {c.end:.1f}s  ({c.duration:.1f}s)  score={c.score}")

    assert len(clips) >= 1, "Should select at least 1 clip"
    for c in clips:
        assert 15.0 <= c.duration <= 45.0, f"Clip duration out of range: {c.duration}"

    print("\n✓ All unit tests passed.")


# ── Integration test ───────────────────────────────────────────

def test_plan_only(job_id: str):
    url = f"{API_BASE}/api/shorts/{job_id}/plan"
    print(f"\n→ POST {url}")
    resp = requests.post(url, timeout=60)
    print(f"← {resp.status_code}")
    data = resp.json()
    print(json.dumps(data, indent=2, ensure_ascii=False))


def test_full_pipeline(job_id: str, add_fade: bool = True):
    url = f"{API_BASE}/api/shorts/{job_id}"
    payload = {"add_fade": add_fade, "count_min": 3, "count_max": 5}

    print(f"\n→ POST {url}")
    print(f"  payload: {payload}\n")

    resp = requests.post(url, json=payload, timeout=300)
    print(f"← {resp.status_code}")
    data = resp.json()
    print(json.dumps(data, indent=2, ensure_ascii=False))

    if data.get("status") == "done":
        print(f"\n✓ Generated {data['total_clips']} shorts:")
        for s in data["shorts"]:
            status = "✓" if s.get("url") else "✗"
            print(f"  {status} Clip {s['index']}: {s['start']}s–{s['end']}s  "
                  f"({s['duration']}s)  score={s['score']}  {s.get('url', 'FAILED')}")
    else:
        print(f"\n✗ Status: {data.get('status')}  Error: {data.get('error')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--unit", action="store_true", help="Run unit tests only")
    parser.add_argument("--job_id", default=None)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--no-fade", action="store_true")
    args = parser.parse_args()

    if args.unit:
        test_scorer_unit()
        sys.exit(0)

    if not args.job_id:
        print("Provide --job_id or --unit")
        parser.print_help()
        sys.exit(1)

    if args.plan_only:
        test_plan_only(args.job_id)
    else:
        test_full_pipeline(args.job_id, add_fade=not args.no_fade)
