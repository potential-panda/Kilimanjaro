"""Durable research planning and execution; imports stay lazy for status/help."""

import json
from pathlib import Path


def register(sub):
    p = sub.add_parser(
        "research", help="Plan and execute durable, bounded discovery campaigns"
    )
    commands = p.add_subparsers(dest="research_command", required=True)
    for name in ("plan", "enqueue"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--campaign", type=Path, required=True)
        cmd.set_defaults(func=_run)
    cmd = commands.add_parser("serve")
    cmd.add_argument("--config", type=Path, required=True)
    cmd.add_argument("--once", action="store_true")
    cmd.set_defaults(func=_run)
    for name in (
        "run",
        "status",
        "explain",
        "retry",
        "backup",
        "summary",
        "freeze",
        "confirm",
        "propose",
        "calibrate",
        "observe",
        "label",
    ):
        cmd = commands.add_parser(name)
        cmd.add_argument(
            "--database", type=Path, default=Path("cycle/17_ledger/research.sqlite")
        )
        if name in ("run", "status", "freeze", "propose", "calibrate"):
            cmd.add_argument("--campaign-id", default=None, help="Resolved run id")
        if name == "run":
            cmd.add_argument("--max-jobs", type=int, default=10)
            cmd.add_argument("--max-minutes", type=float, default=30)
        if name in ("explain", "retry"):
            cmd.add_argument("--job-id", required=True)
        if name == "backup":
            cmd.add_argument("--destination", type=Path, required=True)
        if name == "freeze":
            cmd.add_argument("--candidate-id", required=True)
            cmd.add_argument("--start", required=True)
            cmd.add_argument("--end", required=True)
        if name in ("confirm", "observe", "label"):
            cmd.add_argument("--freeze-id", required=True)
        if name == "propose":
            cmd.add_argument("--limit", type=int, default=5)
            cmd.add_argument("--enqueue", action="store_true")
        if name == "calibrate":
            cmd.add_argument("--scope", type=Path, required=True)
            cmd.add_argument("--offline", type=Path, required=True)
            cmd.add_argument("--online", type=Path, required=True)
            cmd.add_argument("--kind", choices=("factor", "trades"), required=True)
            cmd.add_argument("--tolerance", type=float, default=1e-4)
        if name in ("observe", "label"):
            cmd.add_argument("--input", type=Path, required=True)
        cmd.set_defaults(func=_run)


def _run(args):
    from kilimanjaro.research.contracts import Campaign
    from kilimanjaro.research.planner import enqueue, plan
    from kilimanjaro.research.store import Store

    command = args.research_command
    if command in ("plan", "enqueue"):
        campaign = Campaign.load(args.campaign)
        result = (plan if command == "plan" else enqueue)(campaign)
    elif command in ("run", "serve"):
        import signal

        from kilimanjaro.research.worker import run

        stop = {"requested": False}

        def request_stop(*_):
            stop["requested"] = True

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        if command == "run":
            result = run(
                args.database,
                run_id=args.campaign_id,
                max_jobs=args.max_jobs,
                max_minutes=args.max_minutes,
                stop_requested=lambda: stop["requested"],
            )
        else:
            from kilimanjaro.research.service import load_config, serve

            result = serve(
                load_config(args.config),
                once=args.once,
                stop_requested=lambda: stop["requested"],
            )
    else:
        with Store(
            args.database, readonly=command in ("status", "explain", "summary")
        ) as store:
            if command == "status":
                result = store.status(args.campaign_id)
            elif command == "explain":
                result = store.job(args.job_id)
            elif command == "retry":
                store.retry(args.job_id)
                result = store.job(args.job_id)
            elif command == "backup":
                store.backup(args.destination)
                result = {"backup": str(args.destination)}
            elif command == "summary":
                from kilimanjaro.research.service import summary

                result = summary(store)
            elif command == "freeze":
                from kilimanjaro.research.confirmation import freeze

                result = freeze(
                    store,
                    args.campaign_id,
                    args.candidate_id,
                    start=args.start,
                    end=args.end,
                )
            elif command == "confirm":
                from kilimanjaro.research.confirmation import enqueue_confirmation

                result = enqueue_confirmation(store, args.freeze_id)
            elif command == "propose":
                from kilimanjaro.research.search import enqueue_proposal, propose

                result = propose(store, args.campaign_id, limit=args.limit)
                if args.enqueue:
                    result["runs"] = [
                        enqueue_proposal(store, p["id"]) for p in result["proposals"]
                    ]
            elif command == "calibrate":
                from kilimanjaro.research.calibration import compare_artifacts

                result = compare_artifacts(
                    store,
                    args.campaign_id,
                    json.loads(args.scope.read_text()),
                    args.offline,
                    args.online,
                    kind=args.kind,
                    tolerance=args.tolerance,
                )
            elif command in ("observe", "label"):
                from kilimanjaro.research.observation import mature, record

                result = (record if command == "observe" else mature)(
                    store, args.freeze_id, **json.loads(args.input.read_text())
                )
            if command not in ("status", "explain", "summary"):
                store.publish_events(args.database.parent / "events.jsonl")
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0
