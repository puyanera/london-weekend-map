"""Build the weekend map page from events.json.

events.json is produced by Claude from the weekly Dads in London email
(see EXTRACTION.md). This script does the deterministic part: geocode
postcodes, group events by venue, and write docs/data.json. The page itself
(docs/index.html) is static: a Leaflet map that loads live OpenStreetMap
tiles and reads data.json in the browser, served by GitHub Pages.

Usage: python build_map.py [events.json] [docs/data.json]
"""
import json
import sys
from pathlib import Path

import requests

HERE = Path(__file__).parent


CACHE = HERE / "geocache.json"
LONDON = (51.2, 51.8, -0.6, 0.4)  # lat min/max, lng min/max sanity box for hand-supplied coordinates


def load_cache():
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def geocode(postcodes, cache):
    """postcode -> (lat, lng, precise).

    Order: saved cache (geocache.json), then postcodes.io, with the outcode
    centroid as the fallback for postcodes it does not know. If postcodes.io
    cannot be reached (for example a sandbox with a network allowlist), the
    postcodes it would have resolved are left out and handled by the caller.
    """
    out = {pc: tuple(cache[pc]) for pc in postcodes if pc in cache}
    todo = [pc for pc in postcodes if pc not in out]
    if not todo:
        return out
    try:
        r = requests.post("https://api.postcodes.io/postcodes", json={"postcodes": todo}, timeout=20)
        r.raise_for_status()
        for item in r.json()["result"]:
            if item["result"]:
                out[item["query"]] = (item["result"]["latitude"], item["result"]["longitude"], True)
        for pc in todo:
            if pc in out:
                continue
            resp = requests.get(f"https://api.postcodes.io/outcodes/{pc.split()[0]}", timeout=20)
            if resp.ok and resp.json().get("result"):
                res = resp.json()["result"]
                out[pc] = (res["latitude"], res["longitude"], False)
    except requests.RequestException as exc:
        print(f"postcodes.io not reachable ({exc.__class__.__name__}): {len(todo)} postcode(s) not looked up")
    return out


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "events.json"
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "docs" / "data.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    events = data["events"]

    cache = load_cache()
    coords = geocode(sorted({e["postcode"] for e in events}), cache)
    new = {pc: list(v) for pc, v in coords.items() if pc not in cache}
    if new:
        cache.update(new)
        rows = [f'{json.dumps(pc)}: {json.dumps(v)}' for pc, v in sorted(cache.items())]
        CACHE.write_text("{\n" + ",\n".join(rows) + "\n}\n", encoding="utf-8")
        print(f"added {len(new)} postcode(s) to {CACHE.name}")

    # Last resort: approximate coordinates supplied in events.json (flagged as approximate on the map).
    manual = []
    for e in events:
        pc = e["postcode"]
        lat, lng = e.get("lat"), e.get("lng")
        if pc not in coords and isinstance(lat, (int, float)) and isinstance(lng, (int, float)) \
                and LONDON[0] <= lat <= LONDON[1] and LONDON[2] <= lng <= LONDON[3]:
            coords[pc] = (lat, lng, False)
            manual.append(e["venue"])
    if manual:
        print("using supplied approximate coordinates for:", ", ".join(sorted(set(manual))))

    unplaced = [e for e in events if e["postcode"] not in coords]
    events = [e for e in events if e["postcode"] in coords]

    venues = {}
    for e in events:
        key = (e["postcode"], e["venue"].lower())
        v = venues.setdefault(key, {
            "name": e["venue"], "address": e["address"], "postcode": e["postcode"],
            "lat": coords[e["postcode"]][0], "lng": coords[e["postcode"]][1],
            "approx": not coords[e["postcode"]][2], "events": [],
        })
        v["events"].append({k: e.get(k, "") for k in
                            ("title", "url", "when", "price", "age", "category", "featured", "blurb")})
    venue_list = list(venues.values())

    counts = {c: sum(1 for e in events if e["category"] == c) for c in ("weekend", "ongoing", "later")}
    payload = {"source": data["source"], "venues": venue_list, "counts": counts}
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"{len(events)} events at {len(venue_list)} venues -> {dst}")
    print("counts:", json.dumps(counts))
    approx = [v["name"] for v in venue_list if v["approx"]]
    if approx:
        print("pinned at outcode centre only:", ", ".join(approx))
    if unplaced:
        print("could not place:", ", ".join(e["title"] for e in unplaced))


if __name__ == "__main__":
    main()
