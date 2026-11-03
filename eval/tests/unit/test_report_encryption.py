from __future__ import annotations

import pytest

from bnbagent_eval.privacy import decrypt_report, encrypt_report, generate_keypair

pytest.importorskip("nacl", reason="needs the [crypto] extra")


def test_encrypt_to_several_recipients_and_reject_others() -> None:
    (priv_a, pub_a), (priv_b, pub_b), (priv_c, _) = (generate_keypair() for _ in range(3))
    doc = {"verdict": "pass", "output": "secret deliverable ✓", "score": 0.9}
    env = encrypt_report(doc, [pub_a, pub_b])
    assert "secret deliverable" not in str(env) and len(env["recipients"]) == 2
    assert decrypt_report(env, priv_a) == doc == decrypt_report(env, priv_b)
    with pytest.raises(ValueError, match="not a recipient"):
        decrypt_report(env, priv_c)
    tampered = dict(env, ciphertext=env["ciphertext"][:-8] + "AAAAAAA=")
    with pytest.raises(ValueError):
        decrypt_report(tampered, priv_a)
    with pytest.raises(ValueError):
        encrypt_report(doc, [])
    with pytest.raises(ValueError, match="scheme"):
        decrypt_report(dict(env, scheme="other"), priv_a)


async def test_gate_and_publisher_encrypt_reports() -> None:
    import json

    from bnbagent_eval.privacy import decrypt_report
    from tests.unit.chain import test_workflows as wf

    priv, pub = generate_keypair()
    storage, commerce, _, _, content = await wf._world()
    gate = wf._gate(commerce, storage, content, report_storage=storage, encrypt_to=(pub,))
    await gate(1)
    await gate.wait()
    ev = commerce.submissions[0]["metadata"]["eval"]
    stored = storage.docs[ev["report"]["uri"]]
    assert stored["scheme"].startswith("nacl") and "BNB fees" not in json.dumps(stored)
    assert decrypt_report(stored, priv)["verdict"]["status"] == "pass"
