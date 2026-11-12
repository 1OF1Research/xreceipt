#!/usr/bin/env python3
"""XReceipt × PayPal sandbox — runnable end-to-end demo.

Performs the whole flow against PayPal's sandbox API and prints the
receipt plus its verification, which re-reads PayPal's own records:

    token → create order → (payer approves) → capture → receipt → verify

Setup — you need a FREE PayPal sandbox app (developer.paypal.com →
Apps & Credentials → Sandbox → Create App). Export its credentials:

    export PAYPAL_SANDBOX_CLIENT_ID=…
    export PAYPAL_SANDBOX_CLIENT_SECRET=…

or put them in a file and pass --env-file PATH (KEY=VALUE lines).
Credentials are read from the environment only; they are never printed.

Usage:
    python3 demo_paypal.py [--amount 10.00] [--currency USD]
                           [--description "…"] [--wait 600]
                           [--receipt-out receipt.json] [--env-file PATH]

Approval: a fresh sandbox order starts CREATED. A payer approves it by
opening the printed approve URL and signing in with a sandbox personal
(buyer) account from the same developer dashboard — that is PayPal's own
checkout step, not something a server can do for the payer. With
--wait N this demo polls PayPal until the order reads APPROVED, then
captures. Without --wait it prints the approve URL and the exact resume
command and exits 3.

    python3 demo_paypal.py capture ORDER_ID [--receipt-out receipt.json]

verifies an order that was approved after the fact: captures it,
issues the receipt, verifies it, prints both.

    python3 demo_paypal.py verify receipt.json

re-checks an issued receipt against PayPal's records, any time.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paypal_sandbox as pp  # noqa: E402


def load_env_file(path):
    """Load KEY=VALUE lines into os.environ (existing values win).
    Used only for the two sandbox credential variables; values are
    never printed."""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def print_checks(checks):
    for name, passed, detail in checks:
        mark = "PASS" if passed else "FAIL"
        print(f"{mark} {name}" + (f" — {detail}" if detail else ""))


def print_receipt(receipt):
    print("\n--- XReceipt (PayPal sandbox) ---")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    print("---------------------------------")


def wait_for_approval(client, order, wait_seconds):
    """Poll PayPal until the order is APPROVED (or COMPLETED), the
    wait expires, or the order reaches a state that cannot proceed."""
    deadline = time.monotonic() + wait_seconds
    current = order
    while current.get("status") in ("CREATED", "PAYER_ACTION_REQUIRED"):
        if time.monotonic() >= deadline:
            return current
        time.sleep(5)
        current = client.get_order(order["id"])
    return current


def finish_from_order(client, order, description, receipt_out):
    """Capture an APPROVED order (or take a COMPLETED order as-is),
    issue the receipt, verify it."""
    if order.get("status") == "COMPLETED":
        captured = order
    else:
        captured = client.capture_order(order["id"])
    capture = pp.first_capture(captured)
    if capture is None:
        print("FAIL capture response named no capture")
        return 1
    print(f"captured: capture {capture['id']} · "
          f"{capture['amount']['value']} {capture['amount']['currency_code']}"
          f" · status {capture['status']}")
    receipt = pp.issue_receipt(captured, capture, description)
    print_receipt(receipt)
    if receipt_out:
        with open(receipt_out, "w", encoding="utf-8") as f:
            json.dump(receipt, f, indent=2, sort_keys=True)
        print(f"receipt written to {receipt_out}")
    print("\nverifying against PayPal's own records …")
    ok, checks = pp.verify_receipt(client, receipt)
    print_checks(checks)
    print("RECEIPT VERIFIED" if ok else "RECEIPT FAILED VERIFICATION")
    return 0 if ok else 1


def cmd_run(args, client):
    order = client.create_order(args.amount, args.currency, args.description)
    print(f"order created: {order['id']} · status {order['status']}")
    link = pp.approve_link(order)
    if order.get("status") == "CREATED" and link:
        print(f"approve this order with a sandbox payer account:\n  {link}")
    if order.get("status") in ("CREATED", "PAYER_ACTION_REQUIRED"):
        if args.wait <= 0:
            print("\nnot capturing yet — the order is not approved. "
                  "Approve it, then run:")
            print(f"  python3 {os.path.basename(__file__)} capture {order['id']}")
            return 3
        print(f"waiting up to {args.wait}s for approval …")
        order = wait_for_approval(client, order, args.wait)
        print(f"order status now: {order.get('status')}")
    if order.get("status") not in ("APPROVED", "COMPLETED"):
        print(f"FAIL order is {order.get('status')}, not APPROVED")
        return 1
    return finish_from_order(client, order, args.description, args.receipt_out)


def cmd_capture(args, client):
    order = client.get_order(args.order_id)
    print(f"order {order['id']} · status {order['status']}")
    if order.get("status") == "COMPLETED":
        return finish_from_order(client, order, args.description,
                                 args.receipt_out)
    if order.get("status") != "APPROVED":
        link = pp.approve_link(client.get_order(args.order_id))
        print("order is not approved yet"
              + (f". Approve it first:\n  {link}" if link else ""))
        return 3
    return finish_from_order(client, order, args.description, args.receipt_out)


def cmd_verify(args, client):
    with open(args.receipt_file, "r", encoding="utf-8") as f:
        receipt = json.load(f)
    ok, checks = pp.verify_receipt(client, receipt)
    print_checks(checks)
    print("RECEIPT VERIFIED" if ok else "RECEIPT FAILED VERIFICATION")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env-file", default=None,
                    help="path to a KEY=VALUE file with the sandbox credentials")
    sub = ap.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="create → approve → capture → receipt → verify")
    p_run.add_argument("--amount", default="10.00")
    p_run.add_argument("--currency", default="USD")
    p_run.add_argument("--description", default="XReceipt sandbox payment")
    p_run.add_argument("--wait", type=int, default=0,
                       help="seconds to poll for payer approval (0 = don't wait)")
    p_run.add_argument("--receipt-out", default=None)

    p_cap = sub.add_parser("capture", help="capture an approved order and verify")
    p_cap.add_argument("order_id")
    p_cap.add_argument("--description", default="XReceipt sandbox payment")
    p_cap.add_argument("--receipt-out", default=None)

    p_ver = sub.add_parser("verify", help="re-verify an issued receipt file")
    p_ver.add_argument("receipt_file")

    args = ap.parse_args(argv)
    if args.command is None:
        args.command = "run"
        # re-parse defaults for the implicit run command
        args.amount, args.currency = "10.00", "USD"
        args.description = "XReceipt sandbox payment"
        args.wait, args.receipt_out = 0, None
    if args.env_file:
        load_env_file(args.env_file)

    try:
        client = pp.client_from_env()
    except ValueError as e:
        print(f"FAIL {e}")
        return 2

    try:
        if args.command == "run":
            return cmd_run(args, client)
        if args.command == "capture":
            return cmd_capture(args, client)
        return cmd_verify(args, client)
    except pp.PayPalError as e:
        print(f"FAIL PayPal sandbox error — HTTP {e.status} {e.name}: {e.message}")
        if e.name == "ORDER_NOT_APPROVED":
            print("the order has not been approved by a payer yet; "
                  "approve it in the sandbox, then use the capture command")
        return 1


if __name__ == "__main__":
    sys.exit(main())
