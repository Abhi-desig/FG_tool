# Licence register — Focus Toolkit

**Status:** Draft for review · **Last updated:** 2026-08-22

This matters more here than in a hobby project: **the shop sells the output to clients.** A licence
mistake is commercial risk, not a technical footnote.

---

## The clarification that resolves most of the worry

**Copyleft (GPL, LGPL, AGPL) is triggered by *distributing the software*, not by *selling work made
with it*.**

Posters, banners and translated catalogues produced with this tool are **your work**. You can sell
them freely no matter what licence the tools carry. GIMP is GPL and every design shop on earth sells
GIMP output legally.

What the licences actually govern is what happens if you one day **give this app to another shop**:

| Licence | Using it in-house | Shipping the app to someone else |
|---|---|---|
| MIT / BSD / Apache | Fine | Fine — keep the notice |
| **LGPL-3.0** | Fine | Fine, if kept a separate installable dependency (not vendored/statically linked) |
| **GPL-3.0** | Fine | Your app must also be GPL |
| **AGPL-3.0** | Fine | Your app must be AGPL — **even if only served over a network** |
| Non-commercial | **Not fine** — commercial use is the whole point here | Not fine |

The app is currently for one shop and is not distributed. The rules below keep the option of
distributing it open anyway, because closing that door by accident would be avoidable.

---

## Hard bans

**Enforced as rules in [CLAUDE.md](CLAUDE.md). Do not weaken without a new ADR.**

### 🚫 Never copy Upscayl source — AGPL-3.0
`github.com/upscayl/upscayl` may be read for reference and ideas. Copying its code would force this
entire app to be AGPL. **Use the Real-ESRGAN model weights directly instead** — those are BSD-3 and
are what Upscayl itself wraps.

### 🚫 Never use `bria-rmbg` weights — paid commercial licence
Requires a paid licence for commercial use. It is easy to select by accident because rembg supports
it. Prevented by locking the constant:

```python
MODEL = "birefnet-general"   # never change this — see ADR-007
```

### 🚫 Never use FLUX Kontext dev — non-commercial licence
Non-commercial only. Disqualified outright, since every use here is commercial.

---

## Register

### Phase 1 — Malayalam conversion

| Component | Licence | Status |
|---|---|---|
| `unicode-conversion-maps` (`ML-TTKarthika.map`) | Community-maintained, no LICENSE file; README explicitly invites reuse across tools | ✅ **Vendored.** Attribution kept in the file header |
| `libindic/payyans` | **LGPL-3.0** | ⚠️ Optional spike only. Fine as a pip dependency — **never vendor or copy its source** |

The original plan listed the Payyans licence as an open question. **It is resolved: LGPL-3.0.**
Licensing was never the blocker — the package being abandoned since 2013 was ([ADR-004](DECISIONS.md)).

### Phase 2 — Images

| Component | Licence | Status |
|---|---|---|
| `rembg` (≥2.0.81) | MIT | ✅ |
| BiRefNet weights | MIT | ✅ **Locked** as `birefnet-general` |
| Real-ESRGAN | BSD-3 | ✅ Weights only. ONNX export from `qualcomm/Real-ESRGAN-x4plus`, pinned to an immutable commit and SHA-256 verified. Qualcomm's LICENSE adds no terms — it defers to the BSD-3 original (checked 2026-08-22). See ADR-014 |
| GFPGAN (face restore) | Apache-2.0 | ✅ Not scheduled |
| IOPaint (inpainting) | Apache-2.0 | ✅ Not scheduled |
| Upscayl | AGPL-3.0 | 🚫 **Reference only** |
| `bria-rmbg` | Paid commercial | 🚫 **Banned** |

### Phase 3 — Translation

| Component | Licence | Status |
|---|---|---|
| IndicTrans2 | MIT | ✅ |
| `indictrans2-en-indic-dist-200M` | MIT | ✅ |
| `IndicTransToolkit` (1.1.1) | MIT | ✅ Required — the tokenizer lives here now |
| `openpyxl` | MIT | ✅ |
| PyTorch / transformers | BSD-3 / Apache-2.0 | ✅ |

### Phase 4–5 — Posters and AI

| Component | Licence / terms | Status |
|---|---|---|
| Fabric.js v6 | MIT | ✅ |
| `google-genai` SDK | Apache-2.0 | ✅ |
| Gemini API output | Google's terms — commercial use permitted | ✅ See watermark note |
| fal.ai / Replicate | Per-service terms | ⚠️ Check before use — fallback only |

### Core stack

| Component | Licence |
|---|---|
| Python, FastAPI, uvicorn, Pydantic | PSF / MIT / BSD |
| ONNX Runtime | MIT |
| Pillow | MIT-CMU |
| React, Vite, TypeScript, Tailwind | MIT |
| **shadcn/ui** | MIT — designed to be copied into your repo. No attribution obligation |

### Fonts — check before shipping

| Font | Use | Note |
|---|---|---|
| **ML-TTKarthika** | Client output | Proprietary. The shop must hold its own valid licence — **this tool does not grant one and must not redistribute the font file** |
| Manjari / Noto Sans Malayalam | UI display | OFL — free to bundle |

⚠️ **The one open item.** ML-TTKarthika is a commercial font. This app converts *text encoding* for
it; it neither contains nor distributes the font. Confirm the shop's own licence covers commercial
print use.

---

## AI output disclosure

Google embeds an invisible **SynthID watermark** in AI-generated images. It does not affect printing
or ownership, but the operator should know before a client asks.

---

## On adding anything new

1. Find the licence **before** installing — not after it is wired in.
2. MIT / BSD / Apache → fine, add it here.
3. GPL / LGPL / AGPL → **stop and ask.**
4. "Non-commercial", "research only", "requires a licence for commercial use" → **reject.**
5. Model weights carry their own licence, separate from the code that loads them. Check both.
