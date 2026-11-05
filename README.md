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
| **PayPal** | Payments ride PayPal rails (PYUSD); each receipt is bound to its settlement. |
| **Agents** | Built for the agent economy — any model, any framework, paying per call. |

**Try it live:** https://aemb.pro · **Demo:** https://youtu.be/1_okBp4JAG0

## Verify a receipt yourself

No dependencies, no keys, no signup — just Python 3.

```
python3 verify.py emb_afea0952fd1f47c68c04643a
```

It checks four things, in order: the receipt page names a hash and a
signature; the machine's canonical bytes hash to that hash; the signature
recovers to the published signer; the settlement exists on Base. Pass any
receipt id you like — a forged one fails cleanly.

## For judges

Thanks for your time. The code is private — it *is* the business — but
everything worth judging is live above: no setup, no keys, every receipt
independently verifiable. Need code access to judge fairly? Reach out and
we'll work it out.

---

<div align="center">

MIT — see LICENSE. 🐦‍⬛

</div>
