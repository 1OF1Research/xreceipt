#!/usr/bin/env python3
"""XReceipt × PayPal Developer Platform (sandbox) — a real integration.

What this module does, end to end, against PayPal's sandbox API
(https://api-m.sandbox.paypal.com — the ONLY host it will ever call):

  1. OAuth2 client-credentials token      POST /v1/oauth2/token
  2. Create an order (intent CAPTURE)     POST /v2/checkout/orders
  3. Capture the order's payment          POST /v2/checkout/orders/{id}/capture
  4. Issue an XReceipt whose settlement reference IS the real PayPal
     sandbox capture id (and order id) returned by the API.
  5. Verify: re-read PayPal's own records — GET /v2/payments/captures/{id}
     and GET /v2/checkout/orders/{id} — and check the capture still
     matches the receipt (status, amount, currency, order linkage).

The receipt therefore proves itself against PayPal's records, not
against anything we control.

Standard library only (urllib) — same discipline as verify.py.
Credentials come from the environment:
    PAYPAL_SANDBOX_CLIENT_ID
    PAYPAL_SANDBOX_CLIENT_SECRET
They are never hardcoded, never written to disk by this code, and never
printed. The production PayPal host is not referenced anywhere in
this file: this integration is sandbox-only by construction.
"""

import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from decimal import Decimal, InvalidOperation

SANDBOX_BASE = "https://api-m.sandbox.paypal.com"
TOKEN_PATH = "/v1/oauth2/token"
ORDERS_PATH = "/v2/checkout/orders"
CAPTURES_PATH = "/v2/payments/captures"
HTTP_TIMEOUT = 30

ENV_CLIENT_ID = "PAYPAL_SANDBOX_CLIENT_ID"
ENV_CLIENT_SECRET = "PAYPAL_SANDBOX_CLIENT_SECRET"

# The digital-good checkout shape, per PayPal's Orders v2 reference
# (order_application_context in the published API schema, read
# 2026-11-12): XReceipt is a digital receipt — nothing ships — so the
# order opts out of the shipping flow entirely, asks for the Pay Now
# button flow, and gives PayPal somewhere real to send the payer back
# to on approve/cancel. Without this, PayPal defaults to the
# GET_FROM_FILE shipping flow.
DIGITAL_APPLICATION_CONTEXT = {
    "shipping_preference": "NO_SHIPPING",
    "user_action": "PAY_NOW",
    "return_url": "https://aemb.pro/xreceipt/",
    "cancel_url": "https://aemb.pro/xreceipt/",
}


class PayPalError(Exception):
    """A PayPal API error, carried in PayPal's own fields.

    PayPal's Orders/Payments errors use {name, message, details, debug_id};
    the OAuth token endpoint uses {error, error_description}. Both shapes
    are normalized here. The client secret is never part of an error.
    """

    def __init__(self, status, body):
        self.status = status
        self.body = body if isinstance(body, dict) else {}
        self.name = self.body.get("name") or self.body.get("error") or "HTTP_ERROR"
        self.message = (self.body.get("message")
                        or self.body.get("error_description") or "")
        self.debug_id = self.body.get("debug_id")
        super().__init__(f"PayPal sandbox HTTP {status} {self.name}: {self.message}")


# ------------------------------------------------------------------ transport
def urllib_transport(method, url, headers, body):
    """Default transport: (method, url, headers, body-bytes|None)
    -> (status:int, parsed-json:dict|None, raw:bytes). Never raises for
    HTTP error statuses; raises only on network failure."""
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            raw = r.read()
            return r.status, _parse_json(raw), raw
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, _parse_json(raw), raw


def _parse_json(raw):
    try:
        doc = json.loads(raw)
    except Exception:
        return None
    return doc if isinstance(doc, dict) else None


# --------------------------------------------------------------------- client
class SandboxClient:
    """PayPal sandbox REST client (Orders v2 + Payments v2), stdlib only.

    `transport` is injectable so tests can stand in for the API without
    credentials or network.
    """

    def __init__(self, client_id, client_secret, transport=None,
                 base_url=SANDBOX_BASE):
        if not client_id or not client_secret:
            raise ValueError("client_id and client_secret are required")
        self._client_id = client_id
        self._client_secret = client_secret
        self._transport = transport or urllib_transport
        self._base = base_url.rstrip("/")
        self._token = None
        self._token_expires_at = 0.0  # time.monotonic() deadline

    # -- auth ---------------------------------------------------------------
    def get_access_token(self):
        """OAuth2 client-credentials exchange, per PayPal's authentication
        reference: POST /v1/oauth2/token, HTTP Basic (client_id:secret),
        form body grant_type=client_credentials. Cached until ~60s before
        the server-stated expiry."""
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        basic = base64.b64encode(
            f"{self._client_id}:{self._client_secret}".encode("utf-8")
        ).decode("ascii")
        status, doc, _ = self._transport(
            "POST",
            self._base + TOKEN_PATH,
            {
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            b"grant_type=client_credentials",
        )
        if status != 200 or not doc or not doc.get("access_token"):
            raise PayPalError(status, doc or {})
        self._token = doc["access_token"]
        expires_in = doc.get("expires_in") or 0
        self._token_expires_at = time.monotonic() + max(0, expires_in - 60)
        return self._token

    # -- plumbing -------------------------------------------------------------
    def _request(self, method, path, json_body=None, headers=None):
        hdrs = {
            "Authorization": f"Bearer {self.get_access_token()}",
            "Accept": "application/json",
        }
        body = None
        if json_body is not None:
            hdrs["Content-Type"] = "application/json"
            body = json.dumps(json_body).encode("utf-8")
        if headers:
            hdrs.update(headers)
        status, doc, _ = self._transport(method, self._base + path, hdrs, body)
        if status >= 400 or doc is None:
            raise PayPalError(status, doc or {})
        return doc

    # -- Orders v2 ------------------------------------------------------------
    def create_order(self, amount_value, currency_code="USD",
                     description="XReceipt sandbox payment",
                     reference_id=None, request_id=None,
                     application_context=DIGITAL_APPLICATION_CONTEXT):
        """POST /v2/checkout/orders with intent CAPTURE and one purchase
        unit. Returns the full order representation (id, status, links —
        including the rel=approve URL a payer uses to approve).

        The order carries the digital-good application_context by
        default (see DIGITAL_APPLICATION_CONTEXT): without one, PayPal
        defaults to the GET_FROM_FILE shipping flow — it asks the payer
        for a shipping address nothing ships to, and in the sandbox
        that checkout stalls at the review step. Pass {} or None to
        omit the field entirely."""
        value = normalize_amount(amount_value)
        unit = {
            "reference_id": reference_id or uuid.uuid4().hex,
            "description": description,
            "amount": {"currency_code": currency_code, "value": value},
        }
        body = {"intent": "CAPTURE", "purchase_units": [unit]}
        if application_context:
            body["application_context"] = dict(application_context)
        return self._request(
            "POST", ORDERS_PATH,
            json_body=body,
            headers={
                "Prefer": "return=representation",
                "PayPal-Request-Id": request_id or uuid.uuid4().hex,
            },
        )

    def capture_order(self, order_id, request_id=None, payment_source=None):
        """POST /v2/checkout/orders/{id}/capture. Per PayPal's reference,
        the buyer must first approve the order, or a valid payment_source
        must be provided in this request. Returns the full order
        representation; its purchase_units[].payments.captures[] carry
        the capture id this receipt binds to."""
        body = {}
        if payment_source is not None:
            body["payment_source"] = payment_source
        return self._request(
            "POST", f"{ORDERS_PATH}/{order_id}/capture",
            json_body=body,
            headers={
                "Prefer": "return=representation",
                "PayPal-Request-Id": request_id or uuid.uuid4().hex,
            },
        )

    def get_order(self, order_id):
        """GET /v2/checkout/orders/{id} — PayPal's own record of the order."""
        return self._request("GET", f"{ORDERS_PATH}/{order_id}")

    # -- Payments v2 ------------------------------------------------------------
    def get_capture(self, capture_id):
        """GET /v2/payments/captures/{id} — PayPal's own record of the
        capture (the endpoint named by the capture's own self link)."""
        return self._request("GET", f"{CAPTURES_PATH}/{capture_id}")


def client_from_env(environ=None):
    """Build a SandboxClient from PAYPAL_SANDBOX_CLIENT_ID /
    PAYPAL_SANDBOX_CLIENT_SECRET. Raises ValueError naming the missing
    variables (never their values — there are none to show)."""
    env = os.environ if environ is None else environ
    missing = [k for k in (ENV_CLIENT_ID, ENV_CLIENT_SECRET) if not env.get(k)]
    if missing:
        raise ValueError(
            "missing environment variable(s): " + ", ".join(missing)
            + " — create a free sandbox app at developer.paypal.com and "
              "export its client id and secret under these names")
    return SandboxClient(env[ENV_CLIENT_ID], env[ENV_CLIENT_SECRET])


# ------------------------------------------------------------------ amounts
def normalize_amount(value):
    """Money crosses the API as a string with two decimals ('10.00').
    Accept str/int/Decimal input; return the canonical string. Rejects
    zero, negatives, and non-numeric input — a receipt for nothing is
    not a receipt."""
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"amount is not numeric: {value!r}")
    if d <= 0:
        raise ValueError(f"amount must be positive: {value!r}")
    return str(d.quantize(Decimal("0.01")))


# ------------------------------------------------------------------ receipts
def extract_captures(order):
    """All captures named by an order representation, flattened."""
    out = []
    for unit in order.get("purchase_units") or []:
        payments = unit.get("payments") or {}
        for cap in payments.get("captures") or []:
            out.append(cap)
    return out


def first_capture(order):
    caps = extract_captures(order)
    return caps[0] if caps else None


def order_payee(order):
    """The payee (merchant) named by the order's first purchase unit,
    if PayPal included one: {'merchant_id':…, 'email_address':…}."""
    for unit in order.get("purchase_units") or []:
        payee = unit.get("payee")
        if isinstance(payee, dict) and payee:
            return {k: payee[k] for k in ("merchant_id", "email_address")
                    if payee.get(k)}
    return {}


def canonical_receipt_bytes(receipt):
    """The receipt without its hash field, as canonical JSON bytes —
    the exact bytes receipt_hash commits to."""
    body = {k: v for k, v in receipt.items() if k != "receipt_hash"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def receipt_hash(receipt):
    return hashlib.sha256(canonical_receipt_bytes(receipt)).hexdigest()


def issue_receipt(order, capture, description="XReceipt sandbox payment",
                  issued_at=None):
    """Issue an XReceipt for a captured sandbox order. The settlement
    reference IS the PayPal capture id (and order id) returned by the
    API — that binding is the whole receipt."""
    if not capture or not capture.get("id"):
        raise ValueError("cannot issue a receipt without a capture id")
    amount = capture.get("amount") or {}
    payer = order.get("payer") or {}
    receipt = {
        "type": "xreceipt/paypal-sandbox",
        "version": 1,
        "receipt_id": "xr_" + hashlib.sha256(
            capture["id"].encode("utf-8")).hexdigest()[:24],
        "issued_at": issued_at or time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "description": description,
        "amount": {
            "value": amount.get("value"),
            "currency_code": amount.get("currency_code"),
        },
        "payer": {k: payer[k] for k in ("email_address", "payer_id")
                  if payer.get(k)},
        "payee": order_payee(order),
        "settlement": {
            "network": "paypal-sandbox",
            "order_id": order.get("id"),
            "capture_id": capture["id"],
            "capture_status": capture.get("status"),
            "final_capture": capture.get("final_capture"),
        },
    }
    receipt["receipt_hash"] = receipt_hash(receipt)
    return receipt


# -------------------------------------------------------------- verification
def verify_receipt(client, receipt):
    """Check a sandbox receipt against PayPal's own records.

    Returns (ok, checks) where checks is a list of (name, passed, detail).
    Proves, in order:
      1. the receipt hash recomputes over its canonical bytes
      2. the capture exists at PayPal under the receipt's capture id
      3. the capture's status is COMPLETED
      4. the capture's amount + currency equal the receipt's
      5. the order exists, is COMPLETED, and names this capture
         with the same amount
    """
    checks = []

    def record(name, passed, detail=""):
        checks.append((name, bool(passed), detail))
        return bool(passed)

    st = receipt.get("settlement") or {}
    capture_id = st.get("capture_id")
    order_id = st.get("order_id")
    amount = receipt.get("amount") or {}

    record("receipt hash recomputes",
           receipt.get("receipt_hash") == receipt_hash(receipt),
           receipt.get("receipt_id", "?"))
    if not capture_id or not order_id:
        record("receipt names a capture and an order", False,
               "settlement block incomplete")
        return False, checks
    record("receipt names a capture and an order", True,
           f"capture {capture_id} · order {order_id}")

    try:
        cap = client.get_capture(capture_id)
    except PayPalError as e:
        record("capture exists at PayPal", False, f"{e.name}: {e.message}")
        return False, checks
    record("capture exists at PayPal", True, capture_id)
    record("capture status is COMPLETED", cap.get("status") == "COMPLETED",
           f"status={cap.get('status')}")
    cap_amount = cap.get("amount") or {}
    record("capture amount matches receipt",
           cap_amount.get("value") == amount.get("value")
           and cap_amount.get("currency_code") == amount.get("currency_code"),
           f"paypal {cap_amount.get('value')} {cap_amount.get('currency_code')}"
           f" vs receipt {amount.get('value')} {amount.get('currency_code')}")

    try:
        order = client.get_order(order_id)
    except PayPalError as e:
        record("order exists at PayPal", False, f"{e.name}: {e.message}")
        return False, checks
    record("order exists at PayPal", True, order_id)
    record("order status is COMPLETED", order.get("status") == "COMPLETED",
           f"status={order.get('status')}")
    linked = [c for c in extract_captures(order) if c.get("id") == capture_id]
    record("order names this capture", len(linked) == 1,
           f"{len(linked)} matching capture(s)")
    if linked:
        la = linked[0].get("amount") or {}
        record("order's capture amount matches receipt",
               la.get("value") == amount.get("value")
               and la.get("currency_code") == amount.get("currency_code"),
               f"order capture {la.get('value')} {la.get('currency_code')}")

    return all(p for _, p, _ in checks), checks


# --------------------------------------------------------------- full flow
def approve_link(order):
    for link in order.get("links") or []:
        if link.get("rel") in ("approve", "payer-action"):
            return link.get("href")
    return None


def run_payment_flow(client, amount_value="10.00", currency_code="USD",
                     description="XReceipt sandbox payment",
                     on_order_created=None):
    """token → create → capture → receipt → verify, assuming the order
    is in an approvable state by capture time (approved by the payer, or
    created with a payment source). on_order_created(order) is called
    between create and capture — the demo uses it to print the approve
    URL and wait for approval; tests use it to flip the fake's state.

    Returns (order, capture, receipt, ok, checks). Raises PayPalError
    if capture is rejected (e.g. ORDER_NOT_APPROVED) — the caller
    decides what that means; nothing is hidden here."""
    order = client.create_order(amount_value, currency_code, description)
    if on_order_created is not None:
        on_order_created(order)
    captured = client.capture_order(order["id"])
    capture = first_capture(captured)
    if capture is None:
        raise PayPalError(0, {"name": "NO_CAPTURE",
                              "message": "capture response named no capture"})
    receipt = issue_receipt(captured, capture, description)
    ok, checks = verify_receipt(client, receipt)
    return captured, capture, receipt, ok, checks


_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def looks_like_id(value):
    return bool(value) and bool(_ID_RE.match(str(value)))
