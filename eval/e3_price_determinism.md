# E3 --- Determinism of the money path (price invariant)

We issue the quote path 11 adversarial and benign requests that attempt to influence the signed price through the buyer text.

## Shipped (deterministic) quote path

- Distinct signed prices across 11 requests: **1** (values: [100000000000000000] wei)
- All prices within configured clamp [0, 500000000000000000]: **YES**
- Price invariant to request text: **YES** (no adversarial request moved the price)

The signed price is a pure function of configuration (`clamp(list_price, min, max)`); the buyer's request never enters it, so no injected instruction --- discount, inflation, negative, fake `<price>` tag --- changes the signed value.

## Contrast: LLM-priced path

A quote path that lets the model propose the price (the alternative pattern) is only as safe as the clamp the developer remembers to set. Because pricing is model output there, the signed price *varies with the request* and, absent a ceiling (the scaffold ships `max_price=""` --- no ceiling by default), an injected "price = 999999 U" can be signed. Determinism here is again a construction property, not a behavior to be trusted.

**Result: 1/11 price variance under adversarial input (perfect invariance); 100% within clamp bounds.**
