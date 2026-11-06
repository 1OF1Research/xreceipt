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
| **PayPal** | Built for PayPal-powered agent commerce: PayPal remains the payment record, and XReceipt adds a signed record of the commerce around each payment, bound to its settlement. |
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

## For judges

Thanks for your time. The verifier is public, right here: verify.py
in this repo is the whole instrument, MIT-licensed, no dependencies.
The live system is at https://aemb.pro/xreceipt/ — the same three
files, served next to the demo. Run the check above on any receipt
id and watch it prove itself: no setup, no keys, nothing taken on
trust.

---

<div align="center">

MIT — see LICENSE. 🐦‍⬛

</div>
