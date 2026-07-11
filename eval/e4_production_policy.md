# E4 --- Production-code policy tests

Shipped package: `bnbagent-studio==0.0.2`, `bnbagent==0.3.6`. These calls hit the real `Policy` and the production price-clamp formula (not an eval re-implementation).

| Check | Status | Detail |
|---|---|---|
| clamp: zero -> floor | PASS | got=0 exp=0 |
| clamp: negative -> floor | PASS | got=0 exp=0 |
| clamp: huge 10^30 -> ceiling | PASS | got=200000000000000000 exp=200000000000000000 |
| clamp: uint256-max -> ceiling | PASS | got=200000000000000000 exp=200000000000000000 |
| clamp: in-bounds unchanged | PASS | got=100000000000000000 exp=100000000000000000 |
| clamp: float-literal 0e0 -> floor | PASS | got=0 exp=0 |
| token: allow 'U' | PASS | got=True exp=True |
| token: reject 'USDT' | PASS | got=False exp=False |
| token: reject attacker contract | PASS | got=False exp=False |
| token: reject empty | PASS | got=False exp=False |
| token: reject case-variant 'u' | PASS | got=False exp=False |
| amount: over-cap flagged | PASS | got=True exp=True |
| amount: at-cap allowed | PASS | got=True exp=True |
| amount: negative < cap | PASS | got=True exp=True |

**14/14 PASS.** Clamp bounds `[0, 200000000000000000]` wei; allowed tokens `('U',)`; max/request `5` U.
