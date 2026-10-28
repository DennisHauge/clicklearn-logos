"""
Sync company logos from HubSpot (hs_logo_url) into this repo, so the
company-lookup cards can load them via jsDelivr.

- Reads all companies with a domain and hs_logo_url from HubSpot.
- Saves each logo as logos/<hash>.png, max 128x128 px.
  <hash> = first 20 hex chars of sha256(normalised domain), so the repo
  does not list company names or domains in clear text.
- Only downloads a logo when its hs_logo_url has changed (tracked in
  logos/manifest.json, which stores hash -> logo URL, no domains).
- Prints the files that changed, so the workflow can purge jsDelivr.

Env: HUBSPOT_TOKEN = HubSpot service key or private app token (scope: crm.objects.companies.read)
"""
import hashlib
import io
import json
import os
import sys
import time

import requests
from PIL import Image

API = "https://api.hubapi.com/crm/v3/objects/companies"
LOGO_DIR = os.path.join(os.path.dirname(__file__), "..", "logos")
MANIFEST = os.path.join(LOGO_DIR, "manifest.json")
MAX_SIZE = 128


def norm_domain(d: str) -> str:
    d = (d or "").strip().lower()
    for p in ("https://", "http://"):
        if d.startswith(p):
            d = d[len(p):]
    if d.startswith("www."):
        d = d[4:]
    return d.split("/")[0]


def domain_hash(domain: str) -> str:
    return hashlib.sha256(domain.encode("utf-8")).hexdigest()[:20]


def hubspot_companies(token: str):
    headers = {"Authorization": f"Bearer {token}"}
    params = {"limit": 100, "properties": "domain,hs_logo_url", "archived": "false"}
    after = None
    while True:
        if after:
            params["after"] = after
        r = requests.get(API, headers=headers, params=params, timeout=30)
        if r.status_code == 429:  # rate limited: wait and retry
            time.sleep(10)
            continue
        r.raise_for_status()
        data = r.json()
        for c in data.get("results", []):
            p = c.get("properties", {})
            if p.get("domain") and p.get("hs_logo_url"):
                yield norm_domain(p["domain"]), p["hs_logo_url"]
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break


def to_png(content: bytes) -> bytes:
    img = Image.open(io.BytesIO(content))
    img = img.convert("RGBA")
    img.thumbnail((MAX_SIZE, MAX_SIZE))
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def main():
    token = os.environ.get("HUBSPOT_TOKEN")
    if not token:
        sys.exit("HUBSPOT_TOKEN is not set")
    os.makedirs(LOGO_DIR, exist_ok=True)
    manifest = {}
    if os.path.exists(MANIFEST):
        with open(MANIFEST) as f:
            manifest = json.load(f)

    changed, skipped, failed = [], 0, 0
    seen = set()
    for domain, url in hubspot_companies(token):
        if not domain or domain in seen:
            continue
        seen.add(domain)
        h = domain_hash(domain)
        path = os.path.join(LOGO_DIR, f"{h}.png")
        if manifest.get(h) == url and os.path.exists(path):
            skipped += 1
            continue
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            png = to_png(r.content)
        except Exception as e:  # broken or unsupported logo: skip it
            failed += 1
            print(f"skip {h}: {e}", file=sys.stderr)
            continue
        with open(path, "wb") as f:
            f.write(png)
        manifest[h] = url
        changed.append(f"logos/{h}.png")

    with open(MANIFEST, "w") as f:
        json.dump(manifest, f, indent=0, sort_keys=True)

    print(f"Updated {len(changed)}, unchanged {skipped}, failed {failed}", file=sys.stderr)
    with open(os.environ.get("CHANGED_FILE", "changed.txt"), "w") as f:
        f.write("\n".join(changed))


if __name__ == "__main__":
    main()
