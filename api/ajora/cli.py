import argparse
import os
import time
from contextlib import ExitStack
from datetime import datetime, timezone

from sqlalchemy import select

from .agents import configured_agents
from .collect import Collector, official
from .db import Base, Institute, SessionLocal, Source, engine
from .seed import seed
from .workflow import deadline_alerts, due, refresh, source_due


def crawl(force=False, *, full=False):
    allow_model = bool(configured_agents())
    with SessionLocal() as db:
        ids = due(db, force=force, limit=None if full else 12)
    with ExitStack() as stack:
        collectors = {}
        for source_id in ids:
            with SessionLocal() as db:
                source = db.get(Source, source_id, with_for_update=True)
                if not source or not source_due(source, datetime.now(timezone.utc), force):
                    continue
                institute = db.get(Institute, source.institute_id)
                if institute.id not in collectors:
                    collector = Collector(institute.domains)
                    collectors[institute.id] = collector
                    stack.callback(collector.close)
                outcome = refresh(db, source, collectors[institute.id], allow_model=allow_model)
                db.commit()
                print(f"{source.id} {outcome}", flush=True)
    with SessionLocal() as db:
        deadline_alerts(db)
        db.commit()


def main():
    parser = argparse.ArgumentParser(prog="python -m ajora.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("sources")
    approve = commands.add_parser("source")
    approve.add_argument("institute")
    approve.add_argument("url")
    approve.add_argument("--permission-note", required=True)
    approve.add_argument("--interval", type=int, default=24, choices=range(6, 169), metavar="6–168")
    disable = commands.add_parser("disable")
    disable.add_argument("source_id", type=int)
    monitor = commands.add_parser("crawl")
    monitor.add_argument("--force", action="store_true")
    monitor.add_argument("--full", action="store_true", help="Process all due approved sources")
    monitor.add_argument("--watch", action="store_true", help="Check due sources every 15 minutes without a broker")
    args = parser.parse_args()
    Base.metadata.create_all(engine)
    if args.command == "crawl":
        crawl(args.force, full=args.full or args.watch)
        if args.watch:
            print("Watching approved sources every 15 minutes", flush=True)
            while True:
                try:
                    time.sleep(15 * 60)
                    crawl(full=True)
                except KeyboardInterrupt:
                    break
        return
    with SessionLocal() as db:
        if args.command == "init":
            seed(db, demo=os.getenv("AJORA_LOCAL_DEMO") == "1")
        elif args.command == "sources":
            for source in db.scalars(select(Source).order_by(Source.institute_id, Source.id)):
                print(source.id, source.institute_id, source.url, "enabled" if source.approved else "disabled")
        elif args.command == "disable":
            source = db.get(Source, args.source_id, with_for_update=True)
            if not source:
                parser.error("Unknown source ID")
            source.approved = False
        elif args.command == "source":
            institute = db.get(Institute, args.institute)
            note = args.permission_note.strip()
            if not institute or not official(args.url, institute.domains) or len(note) < 8:
                parser.error("Provide a registered institute, its official HTTPS URL, and a permission note")
            source = db.scalar(select(Source).where(Source.url == args.url))
            if source and source.institute_id != institute.id:
                parser.error("The source belongs to another institute")
            if not source:
                source = Source(institute_id=institute.id, url=args.url)
                db.add(source)
            source.approved, source.permission_note, source.interval_hours = True, note, args.interval
        db.commit()


if __name__ == "__main__":
    main()
