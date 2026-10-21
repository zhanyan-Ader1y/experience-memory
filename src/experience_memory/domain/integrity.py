"""Canonical value fingerprints are version checks, not remote CAS or signatures."""
import hashlib
import json


def canonical(value):
    return json.dumps(value, ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()
