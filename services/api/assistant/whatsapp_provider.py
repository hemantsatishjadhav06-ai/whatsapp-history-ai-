"""Fixed-origin WhatsApp Cloud API transport helpers; no browser-held provider keys."""

import re
from urllib.parse import quote


def graph_endpoint(version: str, account_id: str, operation: str = "") -> str:
    if not re.fullmatch(r"v[0-9]{1,3}\.[0-9]{1,2}", version):
        raise ValueError("Invalid configured WhatsApp API version")
    if not account_id or len(account_id) > 120 or operation not in {"", "messages", "smb_app_data"}:
        raise ValueError("Invalid configured WhatsApp resource")
    # Number IDs come from deployment settings and SQL, never request-selected
    # origins. Encoding also prevents a legacy ID becoming a path/query escape.
    base = f"https://graph.facebook.com/{version}/{quote(account_id, safe='')}"
    return base + (f"/{operation}" if operation else "")


def normalize_phone(value: str) -> str:
    normalized = re.sub(r"[+ ()-]", "", value)
    if not re.fullmatch(r"[1-9][0-9]{5,14}", normalized):
        raise ValueError("Use a full international WhatsApp phone number")
    return normalized
