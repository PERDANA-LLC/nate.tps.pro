#!/usr/bin/env python3
"""Schwab client integration test."""
import os, sys, traceback
proj = '/Volumes/181TB/Perdana-LLC/nate.tps.pro'
sys.path.insert(0, proj)
os.chdir(proj)

from dotenv import load_dotenv
load_dotenv()

from schwab_client import get_client

try:
    c = get_client()
    print(f"OK: Client created: {type(c).__name__}")
    has_token = bool(getattr(c, 'tokens', None) and getattr(c.tokens, 'access_token', None))
    print(f"   Access token set: {'Yes' if has_token else 'No'}")
    
    resp = c.quote("SPY")
    if resp.ok:
        d = resp.json()
        price = d.get('SPY', {}).get('quote', {}).get('lastPrice', '?')
        print(f"OK: SPY=${price}")
    else:
        print(f"WARN: quote HTTP {resp.status_code} {resp.text[:200]}")
    print("DONE")
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")
    traceback.print_exc()
