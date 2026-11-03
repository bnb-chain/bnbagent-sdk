"""Command-line interface: ``bnbagent-eval run | metrics | lint | publish | review | watch |
serve | calibrate | schemas``.

Exit codes for ``run``: 0 pass, 1 fail, 2 inconclusive / pending_human, 64 usage or input error.
``calibrate``: 0 when every judge metric reaches ``--min-kappa``, else 1 (64 on input errors).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from ._version import __version__
from .authoring.lint import lint_suite, lock_suite
from .client import EvalClient
from .engine.dataset import load_cases
from .exceptions import EvalError
from .metrics.registry import list_specs
from .reporting import to_console, to_json, to_markdown
from .types.options import Privacy, RunOptions
from .types.request import EvalRequest
from .types.suite import Suite

EXIT_CODES = {"pass": 0, "fail": 1, "inconclusive": 2, "pending_human": 2}
EXIT_USAGE = 64


def _cmd_metrics(args: argparse.Namespace) -> int:
    if args.markdown or args.check:
        from .authoring.catalog_doc import render_catalog

        text = render_catalog()
        if args.check:
            try:
                current = Path(args.check).read_text(encoding="utf-8")
            except OSError as exc:
                raise EvalError(f"cannot read {args.check}: {exc}") from exc
            if current != text:
                print(
                    f"stale: {args.check} (regenerate: bnbagent-eval metrics --markdown > FILE)",
                    file=sys.stderr,
                )
                return 1
            print(f"ok: {args.check} is up to date")
            return 0
        sys.stdout.write(text)
        return 0
    specs = list_specs()
    if args.third_party:
        from .authoring.catalog_doc import third_party_methods
        from .metrics.vendors import PROVIDERS

        for extra, items in third_party_methods(specs).items():
            p = PROVIDERS[extra]
            print(f"{p.name} ({p.role}): pip install 'bnbagent-eval[{extra}]'")
            print(f"  needs: {p.needs}")
            for metric_id, method in items:
                print(f"  {metric_id:38s} method: {method}")
        return 0
    if args.json:
        print(json.dumps([s.to_dict() for s in specs], indent=2))
        return 0
    for s in specs:
        methods = ", ".join(m.name for m in s.methods)
        print(f"{s.id:40s} v{s.version}  [{methods}]  {s.description}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    suite = Suite.load(args.suite)
    cases = load_cases(args.cases)
    options = RunOptions(privacy=Privacy(report_contents=args.report_contents), runs=args.runs)
    subject = _parse_subject(args.subject, cases) if args.subject else None
    target = _parse_target(args.target, args.target_header) if args.target else None
    request = EvalRequest(
        stage=args.stage,
        suite=suite,
        cases=cases,
        options=options,
        subject=subject,
        target=target,
    )
    response = EvalClient().evaluate_sync(request)
    render = {"md": to_markdown, "console": to_console, "json": to_json}[args.format]
    text = render(response)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
    if args.csv:
        from .reporting.csv import to_csv

        to_csv(response, path=args.csv, per=args.csv_per)
    for backend in args.export or ():
        from .reporting.exporters import export

        export(response, backend)
    if args.diagnose and response.verdict.status != "pass":
        _write_diagnosis(response, cases, args.diagnose)
    return EXIT_CODES[response.verdict.status]


def _write_diagnosis(response, cases, path: str) -> None:
    """Root causes and recommendations for the failing cases: with the configured judge if
    there is one, else rule-based hints from the metric results."""
    import asyncio

    from .analysis.diagnosis import diagnose
    from .config import EvalConfig
    from .judges.providers import create_judge

    cfg = EvalConfig.from_env().judge_config()
    judge = create_judge(cfg) if cfg is not None else None
    result = asyncio.run(diagnose(response, judge=judge, cases=cases))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result.to_dict(), fh, indent=2, ensure_ascii=False)


TARGET_KINDS = ("http", "a2a", "mcp", "callable")


def _parse_target(text: str, headers: Sequence[str] | None = None):
    """``URL`` (an HTTP agent), or ``http:|a2a:|mcp:<tool>@|callable:`` + location.

    ``mcp:<tool>@<url>`` calls one MCP tool; ``callable:module:attr`` calls a Python function.
    ``headers`` are ``Name: value`` strings for http / a2a / mcp targets.
    """
    from .types.target import TargetRef

    kind, _, rest = text.partition(":")
    if kind in ("https", "http") and rest.startswith("//"):
        kind, rest = "http", text  # a bare URL
    elif kind not in TARGET_KINDS:
        raise EvalError(f"--target must be a URL or {'|'.join(TARGET_KINDS)}:...")
    config: dict = {}
    if kind == "mcp":
        tool, sep, rest = rest.partition("@")
        if not sep or not tool:
            raise EvalError("--target mcp:<tool>@<url>")
        config["tool"] = tool
    if headers:
        if kind == "callable":
            raise EvalError("--target-header applies to http, a2a and mcp targets")
        pairs = [h.partition(":") for h in headers]
        if any(not sep or not name.strip() for name, sep, _ in pairs):
            raise EvalError("--target-header must be 'Name: value'")
        config["headers"] = {name.strip(): value.strip() for name, _, value in pairs}
    return TargetRef(kind=kind, uri=rest, config=config)


def _parse_subject(text: str, cases):
    """``agent:<chain_id>:<registry>:<agent_id>[:<agent_version>]`` or
    ``job:<chain_id>:<commerce>:<job_id>:<provider>``.

    A job subject's ``output_hash`` is computed from the single case's output as the ERC-8183
    workflows do (``chain.manifest.content_hash``); several cases or a non-text output are
    ambiguous and rejected.
    """
    from .types.subject import AgentSubject, JobSubject

    kind, _, rest = text.partition(":")
    try:
        if kind == "agent":
            parts = rest.split(":", 3)
            if len(parts) < 3 or not all(parts[:3]):
                raise ValueError("expected agent:<chain_id>:<registry>:<agent_id>[:<version>]")
            version = parts[3] if len(parts) == 4 and parts[3] else None
            return AgentSubject(
                chain_id=int(parts[0]),
                registry=parts[1],
                agent_id=int(parts[2]),
                agent_version=version,
            )
        if kind == "job":
            parts = rest.split(":")
            if len(parts) != 4 or not all(parts):
                raise ValueError("expected job:<chain_id>:<commerce>:<job_id>:<provider>")
            if len(cases) != 1 or not isinstance(cases[0].output, str):
                raise ValueError(
                    "a job subject needs exactly one case with a text output "
                    "(its output_hash is the hash of that deliverable)"
                )
            from .chain.manifest import content_hash

            return JobSubject(
                chain_id=int(parts[0]),
                commerce=parts[1],
                job_id=int(parts[2]),
                provider=parts[3],
                output_hash=content_hash(cases[0].output),
            )
        raise ValueError("the subject must start with 'agent:' or 'job:'")
    except ValueError as exc:
        raise EvalError(f"--subject {text!r}: {exc}") from exc


def _env_judge():
    from .config import EvalConfig
    from .judges.providers import create_judge

    cfg = EvalConfig.from_env().judge_config()
    if cfg is None:
        raise EvalError("no judge configured: set EVAL_JUDGE_* or pass --judge MODULE:FACTORY")
    return create_judge(cfg)


def _cmd_make(args: argparse.Namespace) -> int:
    import asyncio

    from .authoring.eval_maker import EvalMakerError, make_suite

    description = args.description
    if description.startswith("@"):
        with open(description[1:], encoding="utf-8") as fh:
            description = fh.read()
    judge = _load_attr(args.judge)() if args.judge else _env_judge()
    samples = load_cases(args.samples) if args.samples else ()
    try:
        result = asyncio.run(
            make_suite(
                description,
                judge=judge,
                sample_cases=samples,
                stage=args.stage,
                max_reflections=args.max_reflections,
                name=args.name,
            )
        )
    except EvalMakerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        for issue in getattr(exc, "issues", ()) or ():
            print(f"  - {issue}", file=sys.stderr)
        return 2
    if args.out:
        import yaml

        with open(args.out, "w", encoding="utf-8") as fh:
            yaml.safe_dump(result.suite.to_dict(), fh, sort_keys=False, allow_unicode=True)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        for key, why in result.rationale.items():
            print(f"{key}: {why}")
        for issue in result.lint:
            print(f"{issue.level:7s} {issue.code}: {issue.message}")
    return 0


def _cmd_lint(args: argparse.Namespace) -> int:
    suite = Suite.load(args.suite)
    issues = lint_suite(suite, use=args.use)
    if args.json:
        print(json.dumps([i.to_dict() for i in issues], indent=2))
    else:
        for i in issues:
            where = f" [{i.metric}]" if i.metric else ""
            print(f"{i.level:7s} {i.code}{where}: {i.message}")
        if not issues:
            print("ok: no issues")
    if args.lock:
        import yaml

        locked = lock_suite(suite)
        with open(args.lock, "w", encoding="utf-8") as fh:
            yaml.safe_dump(locked.to_dict(), fh, sort_keys=False, allow_unicode=True)
    return 1 if any(i.level == "error" for i in issues) else 0


def _chain_ports(args: argparse.Namespace):
    """``--ports MODULE:FACTORY`` (a ``ChainPorts`` factory: your own adapters, or fakes for a
    dry run), else ports from env vars via the bnbagent adapter (``[bnbagent]`` extra)."""
    if getattr(args, "ports", None):
        return _load_attr(args.ports)()
    from .integrations.bnbagent.env import ports_from_env

    return ports_from_env(args.network)


def _cmd_publish(args: argparse.Namespace) -> int:
    from .chain.erc8004 import MetricsPublisher
    from .types.request import EvalResponse

    try:
        with open(args.response, encoding="utf-8") as fh:
            response = EvalResponse.from_dict(json.load(fh))
    except (OSError, ValueError) as exc:
        raise EvalError(f"{args.response}: not an EvalResponse JSON file ({exc})") from exc
    ports = _chain_ports(args)
    publisher = MetricsPublisher(
        ports.registry, ports.storage, encrypt_to=tuple(args.encrypt_to or ())
    )
    import asyncio

    result = asyncio.run(
        publisher.publish(
            args.agent_id, response, force=args.force, set_metadata_pointer=not args.no_metadata
        )
    )
    print(
        json.dumps(
            {
                "success": result.success,
                "agent_uri": result.agent_uri,
                "tx_hash": result.tx_hash,
                "metadata_tx": result.metadata_tx,
                "report_uri": result.report_uri,
                "error": result.error,
            },
            indent=2,
        )
    )
    return 0 if result.success else 1


def _cmd_review(args: argparse.Namespace) -> int:
    import asyncio

    from .chain.erc8183 import DisputeAdvisor, VoterAssistant

    ports = _chain_ports(args)
    client = EvalClient()

    async def run() -> dict:
        if args.as_ == "voter":
            if ports.signer is None:
                raise EvalError("voting needs a wallet (WALLET_KIND / PRIVATE_KEY ...)")
            advice = await VoterAssistant(
                ports.commerce, ports.policy, ports.storage, client, ports.signer
            ).review(args.job_id)
        else:
            advice = await DisputeAdvisor(
                ports.commerce, ports.policy, ports.storage, client
            ).advise(args.job_id)
        acted = None
        if args.act and advice.action == "dispute":
            acted = await ports.policy.dispute(args.job_id)
        elif args.act and advice.action == "vote_reject":
            acted = await ports.policy.vote_reject(args.job_id)
        return {
            "action": advice.action,
            "reasons": list(advice.reasons),
            "deadline": advice.deadline,
            "provider_evidence": dict(advice.provider_evidence),
            "result_hash": advice.response.result_hash if advice.response else None,
            "tx_hash": acted,
        }

    print(json.dumps(asyncio.run(run()), indent=2, default=str))
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    import asyncio

    from .chain.erc8183 import EvalGate, SqliteGateState, required_watcher_attempts
    from .integrations.bnbagent import watch
    from .integrations.bnbagent.env import network_from_env, ports_from_env, wallet_from_env

    network = network_from_env(args.network)
    work = _load_attr(args.work)
    wallet = wallet_from_env(network)
    if wallet is None:
        raise EvalError("the provider gate needs a wallet (WALLET_KIND / PRIVATE_KEY ...)")
    ports = ports_from_env(network)
    gate = EvalGate(
        ports.commerce,
        ports.storage,
        EvalClient(signer=ports.signer),
        work,
        state=SqliteGateState(args.state),
        max_attempts=args.max_attempts,
        report_storage=ports.storage,
        runs=args.runs,
    )
    attempts = required_watcher_attempts(
        args.max_attempts, args.work_seconds * args.runs, args.interval
    )
    asyncio.run(
        watch(gate, network=network, wallet=wallet, interval=args.interval, max_attempts=attempts)
    )
    return 0


def _load_attr(ref: str):
    import importlib

    module, _, attr = ref.partition(":")
    if not module or not attr:
        raise EvalError(f"{ref!r}: expected MODULE:ATTRIBUTE")
    try:
        obj = importlib.import_module(module)
        for part in attr.split("."):
            obj = getattr(obj, part)
    except (ImportError, AttributeError) as exc:
        raise EvalError(f"cannot load {ref!r}: {exc}") from exc
    return obj


def _gate_from_args(args: argparse.Namespace, store_path: str):
    """The payment gate: ``--gate module:factory``, or X402Gate when ``--pay-to`` is set.

    The built-in gate settles through an x402 facilitator (``--facilitator-url``). Payment
    assets come from ``--assets FILE`` (any EVM chain, no SDK), else from the bnbagent SDK
    catalogue (``[bnbagent]`` extra).
    """
    if args.gate:
        return _load_attr(args.gate)()
    if not args.pay_to:
        return None  # free service (NoPaymentGate)
    if not args.chain_id or not args.facilitator_url:
        raise EvalError("--pay-to needs --chain-id and --facilitator-url")
    from .server.payment import X402Gate
    from .server.settlement import FacilitatorSettlement

    options = {
        "asset": args.asset,
        "base_fee": args.price_base,
        "per_call_fee": args.price_per_judge_call,
        "permit2_spender": args.permit2_spender,
    }
    return X402Gate(
        pay_to=args.pay_to,
        chain_id=args.chain_id,
        assets=_assets_from_args(args),
        settlement=FacilitatorSettlement(args.facilitator_url),
        store_path=str(Path(store_path).with_suffix(".x402.db")),
        **{k: v for k, v in options.items() if v is not None},
    )


def _assets_from_args(args: argparse.Namespace):
    """``--assets FILE``: a YAML / JSON list of ``{chain_id, address, symbol, decimals, methods,
    eip712_domain: {name, version}}``; otherwise the bnbagent SDK's asset catalogue."""
    if not args.assets:
        from .integrations.bnbagent import SdkAssetCatalog

        return SdkAssetCatalog()
    import yaml

    from .ports import AssetSpec, StaticAssetCatalog

    try:
        rows = yaml.safe_load(Path(args.assets).read_text(encoding="utf-8"))
        specs = [
            AssetSpec(
                chain_id=int(r["chain_id"]),
                address=str(r["address"]),
                symbol=str(r["symbol"]),
                decimals=int(r["decimals"]),
                methods=tuple(r.get("methods", ("eip3009",))),
                eip712_domain=r.get("eip712_domain"),
            )
            for r in rows
        ]
    except (OSError, TypeError, KeyError, ValueError, yaml.YAMLError) as exc:
        raise EvalError(f"--assets {args.assets}: {exc}") from exc
    if not any(a.chain_id == args.chain_id for a in specs):
        raise EvalError(f"--assets {args.assets}: no asset for chain {args.chain_id}")
    return StaticAssetCatalog(specs)


def _serve_config(args: argparse.Namespace):
    """``ServiceConfig.from_env()`` with the ``serve`` options applied on top."""
    import dataclasses

    from .server.config import ServiceConfig, parse_kinds

    try:
        config = ServiceConfig.from_env()
        kinds = parse_kinds(args.allowed_kinds) if args.allowed_kinds else config.allowed_kinds
    except ValueError as exc:
        raise EvalError(str(exc)) from exc
    return dataclasses.replace(
        config,
        no_retention=args.no_retention or config.no_retention,
        store_path=args.store or config.store_path,
        base_url=args.base_url or config.base_url,
        allowed_kinds=kinds,
    )


def _cmd_serve(args: argparse.Namespace) -> int:
    from ._optional import require
    from .server.app import create_app

    uvicorn = require("uvicorn", "server")
    config = _serve_config(args)
    signer = None
    if args.signer:
        signer = _load_attr(args.signer)()
    gate = _gate_from_args(args, config.store_path)
    app = create_app(gate=gate, config=config, signer=signer)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


def _read_data_file(path: str) -> object:
    """Parse a user CSV / JSONL / JSON / YAML file; malformed input raises ``EvalError``."""
    import csv

    import yaml

    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
        if p.suffix == ".csv":
            return list(csv.DictReader(text.splitlines()))
        if p.suffix == ".jsonl":
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        return yaml.safe_load(text)  # JSON is YAML
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise EvalError(f"cannot read {path}: {exc}") from exc


def _read_labels(path: str) -> dict[str, object]:
    """Labels file: ``{case_id: label | {metric: label}}`` (JSON / YAML), or rows with
    ``case_id`` (or ``id``), optional ``metric`` and ``label`` (or ``score``) as a JSON / YAML
    list, JSONL or CSV."""
    rows = _read_data_file(path)
    if isinstance(rows, dict):
        return {str(k): v for k, v in rows.items()}
    if not isinstance(rows, list):
        raise EvalError(f"{path}: expected a mapping or a list of label rows")
    out: dict[str, object] = {}
    for row in rows:
        case_id = str(row.get("case_id", row.get("id", "")))
        label = row.get("label", row.get("score"))
        if not case_id:
            raise EvalError(f"{path}: a label row has no case_id")
        if row.get("metric"):
            entry = out.setdefault(case_id, {})
            if not isinstance(entry, dict):
                raise EvalError(f"{path}: case {case_id} has both scalar and per-metric labels")
            entry[str(row["metric"])] = label
        else:
            out[case_id] = label
    return out


def _cmd_calibrate(args: argparse.Namespace) -> int:
    """Judge agreement with human labels; exit 1 if any judge metric has kappa < --min-kappa."""
    import dataclasses

    from .analysis.calibration import MeteredJudge, benchmark_agreement, case_label
    from .engine.resolve import resolve_suite
    from .generation.error_injection import make_balanced_dataset
    from .metrics.base import Kind

    suite = Suite.load(args.suite)
    resolved = resolve_suite(suite)
    judged = [rm for rm in resolved.metrics if rm.method.kind is Kind.JUDGE]
    if not judged:
        raise EvalError("the suite has no judge metrics to calibrate")
    if args.judge:
        judge = _load_attr(args.judge)()
    elif suite.judge is not None:  # the suite's pinned judge, metered for real token counts
        from .judges.providers import create_judge

        judge = create_judge(suite.judge)
    else:
        judge = _env_judge()
    suite = dataclasses.replace(
        suite, metrics=tuple(rm.config for rm in judged), judge=None, lock=None
    )
    cases = load_cases(args.cases)
    injection = None
    if args.inject:
        dataset = make_balanced_dataset(
            cases,
            evaluators=[rm.config.key for rm in judged],
            seed=args.seed,
            faults=args.fault or None,
        )
        cases = dataset.cases
        injection = {
            "seed": args.seed,
            "clean": len(dataset.clean),
            "faulty": len(dataset.faulty),
            "errors": list(dataset.errors),
        }
    labels = _read_labels(args.labels) if args.labels else None
    metered = MeteredJudge(judge)
    request = EvalRequest(stage="dev", suite=suite, cases=tuple(cases))
    response = EvalClient(judge=metered).evaluate_sync(request)
    by_case = {r.case_id: r for r in response.results}
    meta = {c.id: c.metadata for c in cases}
    reports, failures, judge_errors = {}, [], {}
    for rm in judged:
        key = rm.config.key
        try:
            reference = {
                cid: case_label(cid, meta[cid], key, rm.config.id, labels) for cid in meta
            }
        except ValueError as exc:
            raise EvalError(f"{key}: {exc}") from exc
        candidate, errors = {}, []
        for cid, result in by_case.items():
            m = next((m for m in result.metrics if m.name == key), None)
            candidate[cid] = m.score if m is not None and m.status == "scored" else None
            if m is not None and m.status == "error" and reference.get(cid) is not None:
                errors.append(f"{m.error.code}: {m.error.message}" if m.error else "error")
        report = benchmark_agreement(reference, candidate, threshold=args.threshold)
        reports[key] = report
        if errors:  # unscored cases count as disagreements; say why instead of only "kappa"
            judge_errors[key] = len(errors)
            failures.append(f"{key}: {len(errors)} judge error(s), e.g. {errors[0][:200]}")
        elif report.n_compared == 0:
            failures.append(f"{key}: no labelled cases")
        elif report.gate_kappa is None or report.gate_kappa < args.min_kappa:
            failures.append(f"{key}: kappa {report.gate_kappa} < {args.min_kappa}")
    prices = None
    if args.prices:
        prices = _read_data_file(args.prices)
        if not isinstance(prices, dict):
            raise EvalError(f"{args.prices}: expected a mapping model -> prices")
    cost = metered.usage.to_dict(prices)
    out = {
        "suite": suite.name,
        "judge": response.provenance.judge,
        "cases": len(cases),
        "min_kappa": args.min_kappa,
        "passed": not failures,
        "failures": failures,
        "judge_errors": judge_errors,
        "metrics": {k: r.to_dict() for k, r in reports.items()},
        "cost": cost,
        "injection": injection,
    }
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        for key, r in reports.items():
            kappa = "n/a" if r.gate_kappa is None else f"{r.gate_kappa:.3f}"
            mae = "n/a" if r.mae is None else f"{r.mae:.3f}"
            print(f"{key:32s} n={r.n_compared:<4d} {r.score_type:8s} kappa={kappa}  mae={mae}")
        print(f"{'PASS' if not failures else 'FAIL'} (min kappa {args.min_kappa})")
        for f in failures:
            print(f"  - {f}")
    return 0 if not failures else 1


def _default_schema_dir() -> str:
    """``schemas/`` of the source checkout the package runs from, else ``./schemas``."""
    root = Path(__file__).resolve().parent.parent
    return str(root / "schemas") if (root / "pyproject.toml").is_file() else "schemas"


def _cmd_schemas(args: argparse.Namespace) -> int:
    from .server.schemas import write_schemas

    openapi = None
    try:
        from .server.app import create_app
        from .server.config import ServiceConfig
        from .server.store import Store

        app = create_app(config=ServiceConfig(store_path=":memory:"), store=Store(":memory:"))
        openapi = app.openapi()
    except ImportError:
        print("warning: [server] not installed; openapi.json skipped", file=sys.stderr)
    stale = write_schemas(args.dir, openapi=openapi, check=args.check)
    if args.check:
        for name in stale:
            print(f"stale: {name}", file=sys.stderr)
        if not stale:
            print("ok: schemas are up to date")
        return 1 if stale else 0
    for name in stale:
        print(f"wrote {name}")
    return 0


class _Parser(argparse.ArgumentParser):
    """Usage errors exit with 64, not argparse's 2 (which ``run`` uses for inconclusive)."""

    def error(self, message: str):  # type: ignore[override]
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="bnbagent-eval", description="Agent evaluation.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config",
        metavar="FILE",
        help="YAML config file (judge, concurrency); same as EVAL_CONFIG",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_metrics = sub.add_parser("metrics", help="list available metrics")
    p_metrics.add_argument("--json", action="store_true", help="print specs as JSON")
    p_metrics.add_argument(
        "--third-party",
        action="store_true",
        help="list third-party methods (RAGAS, DeepEval, ...) with their install commands",
    )
    p_metrics.add_argument(
        "--markdown", action="store_true", help="print the catalogue as Markdown (docs/METRICS.md)"
    )
    p_metrics.add_argument(
        "--check", metavar="PATH", help="with --markdown: fail if PATH differs from the output"
    )
    p_metrics.set_defaults(func=_cmd_metrics)

    p_run = sub.add_parser("run", help="evaluate cases against a suite")
    p_run.add_argument("--suite", required=True, help="suite file (YAML or JSON)")
    p_run.add_argument("--cases", required=True, help="cases file (JSON, JSONL or YAML)")
    p_run.add_argument(
        "--stage", default="dev", choices=["dev", "registration", "pre_submit", "review", "vote"]
    )
    p_run.add_argument(
        "--subject",
        metavar="SUBJECT",
        help="bind the result to agent:<chain_id>:<registry>:<agent_id>[:<agent_version>] "
        "or job:<chain_id>:<commerce>:<job_id>:<provider> (single case)",
    )
    p_run.add_argument("--format", default="json", choices=["json", "md", "console"])
    p_run.add_argument(
        "--report-contents", default="hashes", choices=["hashes", "redacted", "full"]
    )
    p_run.add_argument("--out", help="write the report to this file")
    p_run.add_argument(
        "--target",
        metavar="TARGET",
        help="call a live agent for each case: URL (HTTP POST {input}), a2a:URL, "
        "mcp:TOOL@URL or callable:module:attr",
    )
    p_run.add_argument(
        "--target-header", action="append", metavar="'Name: value'", help="(repeatable)"
    )
    p_run.add_argument("--runs", type=int, default=1, help="target calls per case")
    p_run.add_argument(
        "--diagnose",
        metavar="PATH",
        help="when the verdict is not pass, write failure root causes and recommendations "
        "(JSON; uses the judge if configured, else rule-based)",
    )
    p_run.add_argument("--csv", metavar="PATH", help="also write a CSV report")
    p_run.add_argument("--csv-per", default="metric", choices=["metric", "case", "summary"])
    p_run.add_argument(
        "--export",
        action="append",
        choices=["otel", "prometheus", "mlflow"],
        help="export telemetry ([exporters] extra; repeatable)",
    )
    p_run.set_defaults(func=_cmd_run)

    p_make = sub.add_parser("make", help="draft a suite from a description (needs a judge)")
    p_make.add_argument("description", help="task description, or @FILE")
    p_make.add_argument("--samples", help="sample cases file")
    p_make.add_argument("--stage", choices=["dev", "registration", "pre_submit", "review", "vote"])
    p_make.add_argument("--max-reflections", type=int, default=2)
    p_make.add_argument("--name", help="suite name")
    p_make.add_argument("--judge", metavar="MODULE:FACTORY", help="judge factory (default: env)")
    p_make.add_argument("-o", "--out", help="write the suite (YAML) here")
    p_make.add_argument("--json", action="store_true")
    p_make.set_defaults(func=_cmd_make)

    p_lint = sub.add_parser("lint", help="check a suite is fit for negotiation / arbitration")
    p_lint.add_argument("suite", help="suite file (YAML or JSON)")
    p_lint.add_argument("--use", default="negotiated", choices=["dev", "negotiated", "service"])
    p_lint.add_argument("--lock", metavar="OUT", help="write the suite with a version lock to OUT")
    p_lint.add_argument("--json", action="store_true")
    p_lint.set_defaults(func=_cmd_lint)

    # Chain commands ([bnbagent] extra; wallet / storage / network from env vars or .env).
    p_pub = sub.add_parser("publish", help="publish a registration evaluation to ERC-8004")
    p_pub.add_argument("response", help="EvalResponse JSON (stage registration)")
    p_pub.add_argument("--agent-id", type=int, required=True)
    p_pub.add_argument(
        "--ports", metavar="MODULE:FACTORY", help="ChainPorts factory (default: SDK from env)"
    )
    p_pub.add_argument("--network", default=None, help="default: $NETWORK or bsc-testnet")
    p_pub.add_argument("--force", action="store_true", help="publish without a pass verdict")
    p_pub.add_argument("--no-metadata", action="store_true", help="skip the metadata pointer")
    p_pub.add_argument("--encrypt-to", action="append", metavar="X25519_PUBKEY_HEX")
    p_pub.set_defaults(func=_cmd_publish)

    p_rev = sub.add_parser("review", help="advise on an ERC-8183 job as client or voter")
    p_rev.add_argument("job_id", type=int)
    p_rev.add_argument("--as", dest="as_", choices=["client", "voter"], default="client")
    p_rev.add_argument(
        "--ports", metavar="MODULE:FACTORY", help="ChainPorts factory (default: SDK from env)"
    )
    p_rev.add_argument("--network", default=None)
    p_rev.add_argument(
        "--act",
        action="store_true",
        help="send the dispute / vote_reject transaction (default: dry run)",
    )
    p_rev.set_defaults(func=_cmd_review)

    p_watch = sub.add_parser("watch", help="run the provider EvalGate on funded jobs")
    p_watch.add_argument("--work", required=True, help="module:function producing deliverables")
    p_watch.add_argument("--network", default=None)
    p_watch.add_argument("--state", default=".eval-cache/gate.db")
    p_watch.add_argument("--max-attempts", type=int, default=2)
    p_watch.add_argument("--interval", type=float, default=30)
    p_watch.add_argument(
        "--work-seconds",
        type=float,
        default=120,
        help="expected work + evaluation time (sizes the watcher's retries)",
    )
    p_watch.add_argument(
        "--runs", type=int, default=1, help="work() calls per attempt (for ops.consistency)"
    )
    p_watch.set_defaults(func=_cmd_watch)

    p_serve = sub.add_parser("serve", help="run the eval HTTP service ([server] extra)")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8080)
    p_serve.add_argument("--log-level", default="info")
    p_serve.add_argument("--no-retention", action="store_true", help="never store case contents")
    p_serve.add_argument("--store", help="sqlite path (default: $EVAL_SERVER_STORE)")
    p_serve.add_argument("--base-url", help="public URL for the agent card")
    p_serve.add_argument(
        "--allowed-kinds",
        metavar="KINDS",
        help="method kinds callers may run, e.g. rule,judge,vendor "
        "(default: $EVAL_SERVER_ALLOWED_KINDS or rule,judge)",
    )
    p_serve.add_argument("--gate", metavar="MODULE:FACTORY", help="custom PaymentGate factory")
    p_serve.add_argument("--signer", metavar="MODULE:FACTORY", help="SignerPort factory")
    p_serve.add_argument("--pay-to", help="x402 recipient address (enables payments)")
    p_serve.add_argument("--chain-id", type=int)
    p_serve.add_argument("--asset", help="token address")
    p_serve.add_argument("--price-base", type=int, help="base price (atomic units)")
    p_serve.add_argument(
        "--price-per-judge-call", type=int, help="price per estimated judge call (atomic units)"
    )
    p_serve.add_argument(
        "--assets",
        metavar="FILE",
        help="payment tokens (YAML / JSON list); default: SDK catalogue",
    )
    p_serve.add_argument("--facilitator-url", help="x402 facilitator (settlement) base URL")
    p_serve.add_argument("--permit2-spender", help="x402 Permit2 proxy (enables permit2-exact)")
    p_serve.set_defaults(func=_cmd_serve)

    p_cal = sub.add_parser("calibrate", help="measure judge agreement with human labels")
    p_cal.add_argument("--suite", required=True, help="suite file; its judge metrics are used")
    p_cal.add_argument("--cases", required=True, help="labelled cases (labels in metadata)")
    p_cal.add_argument("--labels", help="labels file (JSON / YAML / JSONL / CSV)")
    p_cal.add_argument("--judge", metavar="MODULE:FACTORY", help="judge factory (default: env)")
    p_cal.add_argument(
        "--inject", action="store_true", help="build a balanced clean / faulty dataset"
    )
    p_cal.add_argument("--seed", type=int, default=0, help="error injection seed")
    p_cal.add_argument("--fault", action="append", help="restrict injected fault types")
    p_cal.add_argument("--threshold", type=float, help="pass threshold for fail detection")
    p_cal.add_argument("--min-kappa", type=float, default=0.4, help="minimum Cohen's kappa")
    p_cal.add_argument("--prices", help="judge price table (JSON / YAML)")
    p_cal.add_argument("--json", action="store_true")
    p_cal.set_defaults(func=_cmd_calibrate)

    p_schemas = sub.add_parser("schemas", help="write / check the JSON Schema + OpenAPI files")
    p_schemas.add_argument(
        "--dir",
        default=_default_schema_dir(),
        help="output directory (default: schemas/ of the source checkout, else ./schemas)",
    )
    p_schemas.add_argument("--check", action="store_true", help="fail if any file is stale")
    p_schemas.set_defaults(func=_cmd_schemas)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.config:  # read by EvalConfig.from_env wherever a command builds its config
        import os

        os.environ["EVAL_CONFIG"] = args.config
    try:
        return args.func(args)
    except EvalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
