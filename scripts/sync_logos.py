"""
Sync company logos from HubSpot (hs_logo_url) into this repo, so the
company-lookup cards can show them.
 
The chat app blocks images from outside addresses in cards, but it does load
stylesheets from jsDelivr and shows images embedded in them. So each logo is
saved as a tiny stylesheet with the image embedded:
 
  logos/<HubSpot company ID>.css
  .cl-logo-img{background:#fff url(data:image/png;base64,...) center/40px no-repeat}
 
- File names are HubSpot company IDs, so the repo doesn't list company names
  or domains.
- Only downloads a logo when its hs_logo_url has changed (tracked in
  logos/manifest.json: company ID -> logo URL).
- Writes the changed files to CHANGED_FILE, so the workflow can refresh jsDelivr.
 
Env: HUBSPOT_TOKEN = HubSpot service key or private app token (scope: crm.objects.companies.read)
"""
import base64
import glob
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
MAX_SIZE = 96   # px, enough for the 52px logo box on sharp screens
 
 
def hubspot_companies(token: str):
    headers = {"Authorization": f"Bearer {token}"}
    params = {"limit": 100, "properties": "hs_logo_url", "archived": "false"}
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
            url = (c.get("properties") or {}).get("hs_logo_url")
            if url:
                yield str(c["id"]), url
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
 
 
def to_png(content: bytes) -> bytes:
    img = Image.open(io.BytesIO(content)).convert("RGBA")
    img.thumbnail((MAX_SIZE, MAX_SIZE))
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()
 
 
def to_css(png: bytes) -> str:
    b64 = base64.b64encode(png).decode("ascii")
    return (".cl-logo-img{background:#fff url(data:image/png;base64,"
            + b64 + ") center/40px no-repeat}\n")
 
 
def main():
    token = os.environ.get("HUBSPOT_TOKEN")
    if not token:
        sys.exit("HUBSPOT_TOKEN is not set")
    os.makedirs(LOGO_DIR, exist_ok=True)
 
    # Old format (logos/<hash>.png) is no longer used: remove it.
    for old in glob.glob(os.path.join(LOGO_DIR, "*.png")):
        os.remove(old)
 
    manifest = {}
    if os.path.exists(MANIFEST):
        with open(MANIFEST) as f:
            manifest = json.load(f)
    # Drop entries from the old hash-based format.
    manifest = {k: v for k, v in manifest.items() if k.isdigit()}
 
    changed, skipped, failed = [], 0, 0
    for cid, url in hubspot_companies(token):
        path = os.path.join(LOGO_DIR, f"{cid}.css")
        if manifest.get(cid) == url and os.path.exists(path):
            skipped += 1
            continue
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            css = to_css(to_png(r.content))
        except Exception as e:  # broken or unsupported logo: skip it
            failed += 1
            print(f"skip {cid}: {e}", file=sys.stderr)
            continue
        with open(path, "w") as f:
            f.write(css)
        manifest[cid] = url
        changed.append(f"logos/{cid}.css")
 
    with open(MANIFEST, "w") as f:
        json.dump(manifest, f, indent=0, sort_keys=True)
 
    print(f"Updated {len(changed)}, unchanged {skipped}, failed {failed}", file=sys.stderr)
    with open(os.environ.get("CHANGED_FILE", "changed.txt"), "w") as f:
        f.write("\n".join(changed))
 
 
if __name__ == "__main__":
    main()
