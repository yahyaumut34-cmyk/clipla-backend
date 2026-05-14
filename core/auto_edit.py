#!/usr/bin/env python3
"""
test_auto_edit.py — Manual integration test for the auto-edit pipeline.

Usage:
    python test_auto_edit.py --job_id YOUR_JOB_ID [--command "boşlukları kes"]

Prerequisites:
    - Backend running: uvicorn main:app --reload
    - A job folder exists at jobs/{job_id}/ with a video file inside
"""

import argparse
import json
import requests

API_BASE = "http://localhost:8000"


def test_auto_edit(job_id: str, command: str = "hepsini yap"):
    url = f"{API_BASE}/api/auto-edit/{job_id}"

    payload = {
        "voice_command": command,
        "edit_plan": {
            "cut_silence": True,
            "remove_fillers": True,
            "add_subtitles": True,
            "subtitle_mode": "burned",
            "add_music": False,
            "silence_threshold_db": -35.0,
            "silence_min_duration_sec": 0.5,
        }
    }

    print(f"\n→ POST {url}")
    print(f"  command: {command}")
    print(f"  payload: {json.dumps(payload, indent=2)}\n")

    resp = requests.post(url, json=payload, timeout=300)

    print(f"← Status: {resp.status_code}")
    data = resp.json()
    print(json.dumps(data, indent=2, ensure_ascii=False))

    if data.get("status") == "done":
        print(f"\n✓ Output URL: {data['output_url']}")
        print(f"  Duration:   {data['duration_sec']:.1f}s")
        print(f"  Cuts:       {data['cuts_applied']}")
        print(f"  Fillers:    {data['fillers_removed']}")
    else:
        print(f"\n✗ Error: {data.get('error')}")


def test_compiler():
    """Test command → edit plan mapping."""
    from core.compiler import command_to_edit_plan

    commands = [
        "boşlukları kes",
        "şeyleri sil",
        "altyazı ekle",
        "arkaya hafif müzik koy",
        "shorts gibi yap",
        "hepsini yap",
        "",
    ]

    print("\n── Compiler tests ──────────────────────────")
    for cmd in commands:
        plan = command_to_edit_plan(cmd)
        print(f"\n  input:  '{cmd}'")
        print(f"  output: {plan.model_dump()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job_id", default=None)
    parser.add_argument("--command", default="hepsini yap")
    parser.add_argument("--compiler-only", action="store_true")
    args = parser.parse_args()

    if args.compiler_only:
        test_compiler()
    elif args.job_id:
        test_auto_edit(args.job_id, args.command)
    else:
        print("Run compiler test only (no backend needed):")
        test_compiler()
        print("\nFor full pipeline test, run:")
        print("  python test_auto_edit.py --job_id YOUR_JOB_ID")
