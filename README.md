<div align="center">

<pre>◆ · ◆ · ◆ · ◆
· ◆ · ◆ · ◆ ·
◆ · ◆ · ◆ · ◆</pre>

# XReceipt

**Every agent payment, signed and undeniable.**

</div>

---

When an agent pays for you, you deserve proof you said yes. XReceipt
wraps every payment in a signed receipt — amount, payee, intent —
verifiable by anyone, forever.

```
ask → quote → approve in your wallet → signed receipt
```

No account, no signup, no API key. Disputed charges become lookups.

| | |
|---|---|
| **PayPal** | Built for PayPal-powered agent commerce: PayPal remains the payment record, and XReceipt adds a signed record of the commerce around each payment, bound to its settlement. This repo also carries a working PayPal Developer Platform integration (sandbox, Orders v2) — see below. |
| **Agents** | Built for the agent economy — any model, any framework, paying per call. |

**Try it live:** https://aemb.pro/xreceipt/ · **Demo:** https://youtu.be/1_okBp4JAG0

## Verify a receipt yourself

No dependencies, no keys, no signup — just Python 3.

```
python3 verify.py emb_afea0952fd1f47c68c04643a
```

It runs seven checks, in order: the crypto itself, offline, before
any network; the signer document and its scheme; the receipt page's
hash and signature; the machine's canonical bytes, hashing to that
hash and naming the receipt id you passed; the signature, recovering
to the published signer; and the linked settlement, confirmed
on-chain (Base). Pass any receipt id you like — a forged one fails
cleanly.

## Built with the PayPal Developer Platform (sandbox)

`paypal_sandbox.py` is a real integration with PayPal's sandbox API —
not a mock. Standard library only, like the verifier. It calls, in
order:

1. `POST /v1/oauth2/token` — OAuth2 client-credentials exchange
2. `POST /v2/checkout/orders` — creates an order (intent `CAPTURE`)
3. `POST /v2/checkout/orders/{id}/capture` — captures the payment
   after the payer approves the order
4. `GET /v2/payments/captures/{id}` and `GET /v2/checkout/orders/{id}`
   — verification re-reads PayPal's own records

The receipt it issues is bound to the settlement itself: its
settlement reference **is** the capture id (and order id) PayPal
returned. Verification then asks PayPal — does this capture exist, is
it `COMPLETED`, do its amount and currency match the receipt, does the
order name it? A receipt that disagrees with PayPal's records fails,
whoever is holding it.

### Run it yourself

You need a free PayPal sandbox app: sign in at
[developer.paypal.com](https://developer.paypal.com), open
**Apps & Credentials → Sandbox**, create an app, and export its
credentials (they are never written to disk by this code):

```
export PAYPAL_SANDBOX_CLIENT_ID=…
export PAYPAL_SANDBOX_CLIENT_SECRET=…
python3 demo_paypal.py run --wait 600 --receipt-out receipt.json
```

The demo prints an approve URL: open it and sign in with a sandbox
personal (buyer) account from the same dashboard — that approval is
PayPal's own checkout step. The demo then captures, prints the
receipt, and verifies it against PayPal's records. Re-check any
receipt later with `python3 demo_paypal.py verify receipt.json`.

No credentials handy? `python3 test_paypal_sandbox.py` runs the whole
flow offline against a fake transport in PayPal's documented response
shapes — 11 checks covering the token exchange, order/capture
parsing, receipt binding, and verification's match and mismatch
cases.

## For judges

Thanks for your time. The verifier is public, right here: verify.py
in this repo is the whole instrument, MIT-licensed, no dependencies.
The live system is at https://aemb.pro/xreceipt/ — the same three
files, served next to the demo. Run the check above on any receipt
id and watch it prove itself: no setup, no keys, nothing taken on
trust. The PayPal sandbox integration above runs with your own free
sandbox app credentials.

---

<div align="center">

MIT — see LICENSE. 🐦‍⬛

</div>
