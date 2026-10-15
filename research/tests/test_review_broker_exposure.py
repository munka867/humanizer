"""Phase-6: what is in the PUBLIC repository (git ls-files), who may import what, and are the docs honest. Read-only checks."""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


def tracked():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("not a git checkout")
    return [f for f in out.stdout.split("\0") if f]


PLACEHOLDER_IDS = {"DU1234567", "U1234567", "U7654321", "DU0000000", "U9999999", "DU1111111", "U2222222", "DUFAKE001", "DU1", "U2"}


def test_no_approval_key_ledger_or_account_export_is_tracked():
    bad = [f for f in tracked() if re.search(r"(paper_approval[^/]*\.json|approvals/[^/]+\.json|\.cc_token|ibkr_exports/|\.sqlite3?$|\.db$|\.pem$|\.env$)", f) or (f.endswith("ledger.db"))]
    assert not bad, bad


def test_tracked_text_has_no_real_looking_account_ids_keys_or_absolute_paths():
    ids, secrets, paths = set(), [], []
    for f in tracked():
        if f.endswith((".png", ".woff2", ".ico", ".parquet")):
            continue
        p = ROOT / f
        try:
            t = p.read_text(errors="ignore")
        except OSError:
            continue
        ids |= {m for m in re.findall(r"\b(?:D[UFI]\d{5,9}|U\d{6,9})\b", t)}
        if re.search(r"CC_APPROVAL_KEY\s*=\s*['\"]?[A-Za-z0-9+/_\-]{16,}|BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY|\"hmac_sha256\"\s*:\s*\"[0-9a-f]{64}\"", t):
            secrets.append(f)
        if re.search(r"/home/[a-z]+/|/Users/[A-Za-z]+/|C:\\\\Users\\\\", t) and not f.startswith("tests/"):
            paths.append(f)
    assert ids <= PLACEHOLDER_IDS, f"non-placeholder account-like ids tracked: {sorted(ids - PLACEHOLDER_IDS)}"
    assert not secrets and not paths, (secrets, paths)


def test_gitignore_covers_approvals_snapshots_and_tokens():
    gi = (ROOT / ".gitignore").read_text() + (ROOT / "command_center" / ".gitignore").read_text()
    for pat in ("configs/approvals/*.json", "data/processed_*/", "data/fake_*/", ".cc_token", "data/raw/ibkr_exports/"):
        assert pat in gi, pat


def test_ib_async_and_order_code_live_only_where_the_contract_says():
    """CLAUDE.md: only brokers/ibkr_paper may import ib_async for orders; command_center/ibkr (read-only wrapper) may import it for data."""
    hits = []
    for f in list((ROOT / "src").rglob("*.py")) + list((ROOT / "command_center").rglob("*.py")) + list((ROOT / "scripts").rglob("*.py")):
        if re.search(r"^\s*(import|from)\s+(ib_async|ib_insync|ibapi)\b", f.read_text(), re.M):
            hits.append(str(f.relative_to(ROOT)))
    assert sorted(hits) == ["command_center/ibkr/readonly_client.py", "src/tradelab/brokers/ibkr_paper/adapter.py"], hits
    adapter = (ROOT / "src/tradelab/brokers/ibkr_paper/adapter.py").read_text()
    assert adapter.count("import ib_async") == 1
    for f in (ROOT / "src").rglob("*.py"):
        t = f.read_text()
        if "placeOrder" in t:
            assert "brokers/ibkr_paper" in str(f), f


def test_execution_and_dashboard_do_not_import_each_other():
    for f in (ROOT / "src" / "tradelab" / "execution").rglob("*.py"):
        assert "command_center" not in f.read_text(), f
    for f in (ROOT / "command_center").glob("*.py"):
        if f.name.startswith(("verify_", "repro")):
            continue
        assert not re.search(r"tradelab\.(execution|brokers)", f.read_text()), f


def test_no_script_wires_a_paper_service_or_places_orders():
    for f in (ROOT / "scripts").glob("*.py"):
        t = f.read_text()
        assert not re.search(r"ExecutionService|IbkrPaperBroker|smoke_test|placeOrder|make_approval", t), f


def test_docs_do_not_claim_real_broker_verification():
    docs = {n: (ROOT / "docs" / n).read_text() for n in ("READINESS.md", "PAPER_SETUP.md", "IBKR_SETUP.md", "EXECUTION_DESIGN.md")}
    assert re.search(r"nothing has ever touched a real or paper broker", docs["READINESS.md"], re.I)
    assert "never run" in docs["IBKR_SETUP.md"].lower() or "not verified" in docs["IBKR_SETUP.md"].lower()
    assert "UNVERIFIED" in docs["PAPER_SETUP.md"]
    for n, t in docs.items():
        assert not re.search(r"(verified|tested|confirmed) (against|with|on) (a )?(real|live) (tws|broker|gateway|account)", t, re.I), n


@pytest.mark.xfail(strict=True, reason="P6-09 (doc inconsistency): the paper adapter's comment and EXECUTION_DESIGN say the data adapter uses clientIds 11-19, but command_center/ibkr/guard.py uses 9100-9109")
def test_documented_client_id_ranges_match_the_code():
    guard_py = (ROOT / "src/tradelab/brokers/ibkr_paper/guard.py").read_text() + (ROOT / "docs/EXECUTION_DESIGN.md").read_text()
    code = (ROOT / "command_center/ibkr/guard.py").read_text()
    m = re.search(r"CLIENT_ID_MIN, CLIENT_ID_MAX, CLIENT_ID_DEFAULT = (\d+), (\d+)", code)
    assert f"{m.group(1)}-{m.group(2)}" in guard_py
