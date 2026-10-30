"""
GitHub webhook HMAC-SHA256 signature validation.

GitHub signs every webhook payload with:
  X-Hub-Signature-256: sha256=<hex_digest>

We verify this using our shared secret. If signatures don't match
we reject the request immediately — prevents spoofed builds.

Common mistake: comparing bytes vs hex string — always compare hex to hex.
"""
import hmac
import hashlib
from fastapi import Request, HTTPException


async def verify_github_signature(request: Request, secret: str) -> bytes:
    """
    Validate GitHub webhook HMAC-SHA256 signature.
    Returns raw body bytes on success. Raises 401 on failure.
    """
    signature_header = request.headers.get("X-Hub-Signature-256", "")
    if not signature_header:
        raise HTTPException(401, "Missing X-Hub-Signature-256 header")

    if not signature_header.startswith("sha256="):
        raise HTTPException(401, "Invalid signature format — expected sha256=...")

    received_sig = signature_header[7:]   # strip "sha256=" prefix

    body = await request.body()

    # Compute expected HMAC using shared secret
    expected_sig = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256
    ).hexdigest()

    # Use constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(received_sig, expected_sig):
        raise HTTPException(401, "Webhook signature mismatch — request rejected")

    return body
