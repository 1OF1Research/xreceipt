#!/usr/bin/env python3
"""Offline tests for paypal_sandbox.py — no credentials, no network.

A fake transport stands in for the PayPal sandbox API and replays
response bodies in the exact shapes PayPal's own Orders v2 / Payments v2
references publish (same field names, same nesting). The tests prove
the module's logic:

  - the OAuth2 token request is shaped exactly as PayPal's
    authentication reference prescribes, and the token is cached
  - order create sends intent CAPTURE + the purchase unit, with the
    representation and idempotency headers
  - capture parsing finds the capture inside the order representation
  - the receipt binds to the real (fake-transport) capture/order ids
    and its hash recomputes
  - verification passes on a match and FAILS on: tampered amount,
    tampered currency, a non-COMPLETED capture, and a missing capture
  - an unapproved order's capture surfaces ORDER_NOT_APPROVED
  - missing credentials raise a clear error naming the variables

Run:  python3 test_paypal_sandbox.py     (exit 0 = all pass)
"""

import base64
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paypal_sandbox as pp  # noqa: E402

CLIENT_ID = "test-client-id"
CLIENT_SECRET = "test-client-secret"
ORDER_ID = "5O190127TN364715T"
CAPTURE_ID = "3C679366HH908993F"


def order_doc(status, with_capture=False):
    """An order representation in the documented Orders v2 shape."""
    doc = {
        "id": ORDER_ID,
        "status": status,
        "intent": "CAPTURE",
        "payment_source": {"paypal": {
            "name": {"given_name": "John", "surname": "Doe"},
            "email_address": "buyer@example.com",
            "account_id": "QYR5Z8XDVJNXQ",
            "account_status": "VERIFIED",
        }},
        "purchase_units": [{
            "reference_id": "ref-1",
            "amount": {"currency_code": "USD", "value": "100.00"},
            "payee": {"email_address": "merchant@example.com",
                      "merchant_id": "MERCHANT123"},
        }],
        "payer": {"name": {"given_name": "John", "surname": "Doe"},
                  "email_address": "buyer@example.com",
                  "payer_id": "QYR5Z8XDVJNXQ"},
        "links": [
            {"href": f"https://api-m.sandbox.paypal.com/v2/checkout/orders/{ORDER_ID}",
             "rel": "self", "method": "GET"},
            {"href": "https://www.sandbox.paypal.com/checkoutnow?token=EC-TEST",
             "rel": "approve", "method": "GET"},
        ],
    }
    if with_capture:
        doc["purchase_units"][0]["payments"] = {"captures": [{
            "id": CAPTURE_ID,
            "status": "COMPLETED",
            "amount": {"currency_code": "USD", "value": "100.00"},
            "final_capture": True,
            "create_time": "2026-11-12T12:00:00Z",
            "links": [{"href": f"https://api-m.sandbox.paypal.com/v2/payments/captures/{CAPTURE_ID}",
                       "rel": "self", "method": "GET"}],
        }]}
    return doc


def capture_doc(status="COMPLETED", value="100.00", currency="USD"):
    return {
        "id": CAPTURE_ID,
        "status": status,
        "amount": {"currency_code": currency, "value": value},
        "final_capture": True,
        "links": [{"href": f"https://api-m.sandbox.paypal.com/v2/payments/captures/{CAPTURE_ID}",
                   "rel": "self", "method": "GET"}],
    }


class FakePayPal:
    """A transport callable standing in for api-m.sandbox.paypal.com."""

    def __init__(self, auto_approve=False):
        self.calls = []
        self.auto_approve = auto_approve
        self.order_status = "APPROVED" if auto_approve else "CREATED"
        self.capture = capture_doc()
        self.capture_visible = True

    def approve(self):
        if self.order_status == "CREATED":
            self.order_status = "APPROVED"

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, dict(headers), body))
        path = url.replace(pp.SANDBOX_BASE, "")

        if path == pp.TOKEN_PATH:
            expected = "Basic " + base64.b64encode(
                f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
            if headers.get("Authorization") != expected:
                return 401, {"error": "invalid_client",
                             "error_description": "Client Authentication failed"}, b""
            if body != b"grant_type=client_credentials":
                return 400, {"error": "invalid_request",
                             "error_description": "bad grant body"}, b""
            if headers.get("Content-Type") != "application/x-www-form-urlencoded":
                return 400, {"error": "invalid_request",
                             "error_description": "bad content type"}, b""
            return 200, {"access_token": "A21FAKE-TOKEN", "token_type": "Bearer",
                         "expires_in": 32400, "app_id": "APP-FAKE"}, b""

        if not headers.get("Authorization", "").startswith("Bearer "):
            return 401, {"name": "AUTHENTICATION_FAILURE",
                         "message": "Authentication failed"}, b""

        if method == "POST" and path == pp.ORDERS_PATH:
            sent = json.loads(body)
            assert sent["intent"] == "CAPTURE", sent
            assert sent["purchase_units"][0]["amount"] == {
                "currency_code": "USD", "value": "100.00"}, sent
            return 201, order_doc(self.order_status), b""

        if method == "POST" and path == f"{pp.ORDERS_PATH}/{ORDER_ID}/capture":
            if self.order_status != "APPROVED":
                return 422, {"name": "UNPROCESSABLE_ENTITY",
                             "message": "The requested action could not be performed.",
                             "details": [{"issue": "ORDER_NOT_APPROVED",
                                          "description": "Order could not be captured."}],
                             "debug_id": "fake-debug"}, b""
            self.order_status = "COMPLETED"
            return 200, order_doc("COMPLETED", with_capture=True), b""

        if method == "GET" and path == f"{pp.ORDERS_PATH}/{ORDER_ID}":
            done = self.order_status == "COMPLETED"
            return 200, order_doc(self.order_status, with_capture=done), b""

        if method == "GET" and path == f"{pp.CAPTURES_PATH}/{CAPTURE_ID}":
            if not self.capture_visible:
                return 404, {"name": "RESOURCE_NOT_FOUND",
                             "message": "The specified resource does not exist."}, b""
            return 200, copy.deepcopy(self.capture), b""

        return 404, {"name": "RESOURCE_NOT_FOUND", "message": f"no route {path}"}, b""


RESULTS = []


def check(name, fn):
    try:
        fn()
    except Exception as e:  # noqa: BLE001 — the harness reports, never hides
        RESULTS.append((name, False, f"{type(e).__name__}: {e}"))
    else:
        RESULTS.append((name, True, ""))


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


# --------------------------------------------------------------------- tests
def t_token_shape_and_cache():
    fake = FakePayPal()
    client = pp.SandboxClient(CLIENT_ID, CLIENT_SECRET, transport=fake)
    tok1 = client.get_access_token()
    tok2 = client.get_access_token()
    expect(tok1 == "A21FAKE-TOKEN" and tok2 == tok1, "token value/cache")
    token_calls = [c for c in fake.calls if c[1].endswith(pp.TOKEN_PATH)]
    expect(len(token_calls) == 1, f"token fetched {len(token_calls)} times, want 1")


def t_bad_credentials_surface_oauth_error():
    fake = FakePayPal()
    client = pp.SandboxClient(CLIENT_ID, "wrong-secret", transport=fake)
    try:
        client.get_access_token()
    except pp.PayPalError as e:
        expect(e.status == 401, f"status {e.status}")
        expect(e.name == "invalid_client", f"name {e.name}")
        expect("wrong-secret" not in str(e), "secret leaked into error")
        return
    raise AssertionError("bad secret did not raise")


def t_unapproved_capture_raises():
    fake = FakePayPal(auto_approve=False)
    client = pp.SandboxClient(CLIENT_ID, CLIENT_SECRET, transport=fake)
    order = client.create_order("100.00", "USD")
    expect(order["status"] == "CREATED", order["status"])
    expect(pp.approve_link(order).startswith("https://www.sandbox.paypal.com/"),
           "approve link missing")
    try:
        client.capture_order(order["id"])
    except pp.PayPalError as e:
        expect(e.status == 422, f"status {e.status}")
        return
    raise AssertionError("unapproved capture did not raise")


def t_full_flow_and_receipt_binding():
    fake = FakePayPal(auto_approve=True)
    client = pp.SandboxClient(CLIENT_ID, CLIENT_SECRET, transport=fake)
    order, capture, receipt, ok, checks = pp.run_payment_flow(
        client, "100.00", "USD", "XReceipt sandbox payment")
    expect(capture["id"] == CAPTURE_ID, "capture id")
    expect(receipt["settlement"]["capture_id"] == CAPTURE_ID, "receipt capture binding")
    expect(receipt["settlement"]["order_id"] == ORDER_ID, "receipt order binding")
    expect(receipt["amount"] == {"value": "100.00", "currency_code": "USD"},
           f"receipt amount {receipt['amount']}")
    expect(receipt["payee"].get("merchant_id") == "MERCHANT123", "payee")
    expect(receipt["payer"].get("email_address") == "buyer@example.com", "payer")
    expect(receipt["receipt_hash"] == pp.receipt_hash(receipt), "hash recompute")
    expect(receipt["receipt_id"].startswith("xr_"), "receipt id form")
    expect(ok, f"verification failed: {checks}")
    expect(all(p for _, p, _ in checks), f"checks: {checks}")
    # create carried the documented headers
    create = [c for c in fake.calls if c[0] == "POST" and c[1].endswith(pp.ORDERS_PATH)][0]
    expect(create[2].get("Prefer") == "return=representation", "Prefer header")
    expect(create[2].get("PayPal-Request-Id"), "idempotency header")


def t_verify_detects_tampered_amount():
    fake = FakePayPal(auto_approve=True)
    client = pp.SandboxClient(CLIENT_ID, CLIENT_SECRET, transport=fake)
    _, _, receipt, ok, _ = pp.run_payment_flow(client, "100.00", "USD")
    expect(ok, "baseline flow should verify")
    bad = copy.deepcopy(receipt)
    bad["amount"]["value"] = "1.00"
    bad["receipt_hash"] = pp.receipt_hash(bad)  # attacker rehashes too
    ok2, checks = pp.verify_receipt(client, bad)
    expect(not ok2, "tampered amount verified!")
    failed = [n for n, p, _ in checks if not p]
    expect(any("amount" in n for n in failed), f"failed checks: {failed}")


def t_verify_detects_tampered_hash():
    fake = FakePayPal(auto_approve=True)
    client = pp.SandboxClient(CLIENT_ID, CLIENT_SECRET, transport=fake)
    _, _, receipt, ok, _ = pp.run_payment_flow(client, "100.00", "USD")
    expect(ok, "baseline")
    bad = copy.deepcopy(receipt)
    bad["amount"]["value"] = "1.00"  # no rehash this time
    ok2, checks = pp.verify_receipt(client, bad)
    expect(not ok2, "tampered receipt verified!")
    expect(checks[0][1] is False, "hash check should fail first")


def t_verify_detects_pending_capture():
    fake = FakePayPal(auto_approve=True)
    client = pp.SandboxClient(CLIENT_ID, CLIENT_SECRET, transport=fake)
    _, _, receipt, ok, _ = pp.run_payment_flow(client, "100.00", "USD")
    expect(ok, "baseline")
    fake.capture = capture_doc(status="PENDING")
    ok2, checks = pp.verify_receipt(client, receipt)
    expect(not ok2, "PENDING capture verified!")
    expect(any("status" in n and not p for n, p, _ in checks), f"{checks}")


def t_verify_detects_currency_mismatch():
    fake = FakePayPal(auto_approve=True)
    client = pp.SandboxClient(CLIENT_ID, CLIENT_SECRET, transport=fake)
    _, _, receipt, ok, _ = pp.run_payment_flow(client, "100.00", "USD")
    expect(ok, "baseline")
    fake.capture = capture_doc(currency="EUR")
    ok2, _ = pp.verify_receipt(client, receipt)
    expect(not ok2, "currency mismatch verified!")


def t_verify_detects_missing_capture():
    fake = FakePayPal(auto_approve=True)
    client = pp.SandboxClient(CLIENT_ID, CLIENT_SECRET, transport=fake)
    _, _, receipt, ok, _ = pp.run_payment_flow(client, "100.00", "USD")
    expect(ok, "baseline")
    fake.capture_visible = False
    ok2, checks = pp.verify_receipt(client, receipt)
    expect(not ok2, "missing capture verified!")
    expect(any("exists" in n and not p for n, p, _ in checks), f"{checks}")


def t_create_order_sends_digital_application_context():
    fake = FakePayPal()
    client = pp.SandboxClient(CLIENT_ID, CLIENT_SECRET, transport=fake)
    client.create_order("100.00", "USD")
    create = [c for c in fake.calls
              if c[0] == "POST" and c[1].endswith(pp.ORDERS_PATH)][0]
    sent = json.loads(create[3])
    ctx = sent.get("application_context")
    expect(ctx == pp.DIGITAL_APPLICATION_CONTEXT, f"application_context: {ctx}")
    expect(ctx["shipping_preference"] == "NO_SHIPPING", "shipping_preference")
    expect(ctx["user_action"] == "PAY_NOW", "user_action")
    expect(ctx["return_url"] == "https://aemb.pro/xreceipt/", "return_url")
    expect(ctx["cancel_url"] == "https://aemb.pro/xreceipt/", "cancel_url")


def t_create_order_can_omit_application_context():
    fake = FakePayPal()
    client = pp.SandboxClient(CLIENT_ID, CLIENT_SECRET, transport=fake)
    client.create_order("100.00", "USD", application_context=None)
    create = [c for c in fake.calls
              if c[0] == "POST" and c[1].endswith(pp.ORDERS_PATH)][0]
    sent = json.loads(create[3])
    expect("application_context" not in sent, f"body should omit it: {sent}")


def t_missing_env_vars_named():
    try:
        pp.client_from_env({})
    except ValueError as e:
        expect(pp.ENV_CLIENT_ID in str(e) and pp.ENV_CLIENT_SECRET in str(e),
               f"error should name both variables: {e}")
        return
    raise AssertionError("empty env did not raise")


def t_amount_normalization():
    expect(pp.normalize_amount("10") == "10.00", "int-string")
    expect(pp.normalize_amount(10.5) == "10.50", "float")
    for bad in ("0", "-1", "abc", "0.00"):
        try:
            pp.normalize_amount(bad)
        except ValueError:
            continue
        raise AssertionError(f"amount {bad!r} accepted")


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("t_") and callable(v)]
    for t in tests:
        check(t.__name__, t)
    failed = 0
    for name, passed, detail in RESULTS:
        print(("PASS " if passed else "FAIL ") + name + (f" — {detail}" if detail else ""))
        failed += 0 if passed else 1
    print(f"{len(RESULTS) - failed}/{len(RESULTS)} tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
