from datetime import datetime, timedelta
from typing import Optional
import hashlib, hmac, base64, json
from .settings import get_settings
settings = get_settings()

def hash_password(p: str) -> str: return hashlib.sha256(p.encode()).hexdigest()
def verify_password(p: str, h: str) -> bool: return hash_password(p) == h

def _b64url(b: bytes) -> str: return base64.urlsafe_b64encode(b).rstrip(b'=').decode()
def _b64url_decode(s: str) -> bytes: return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

def create_jwt(payload: dict, hours: int = None) -> str:
    header = {"alg":"HS256","typ":"JWT"}
    exp_h = hours or settings.JWT_EXPIRES_HOURS
    payload = {**payload, "exp": int((datetime.utcnow()+timedelta(hours=exp_h)).timestamp())}
    hb, pb = _b64url(json.dumps(header,separators=(',',':')).encode()), _b64url(json.dumps(payload,separators=(',',':')).encode())
    sig = _b64url(hmac.new(settings.JWT_SECRET.encode(), f"{hb}.{pb}".encode(), "sha256").digest())
    return f"{hb}.{pb}.{sig}"

def decode_jwt(token: str) -> Optional[dict]:
    try:
        hb, pb, s = token.split(".")
        expect = _b64url(hmac.new(settings.JWT_SECRET.encode(), f"{hb}.{pb}".encode(), "sha256").digest())
        if not hmac.compare_digest(s, expect): return None
        data = json.loads(_b64url_decode(pb))
        if int(data.get("exp",0)) < int(datetime.utcnow().timestamp()): return None
        return data
    except Exception:
        return None
