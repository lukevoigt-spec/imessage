"""Command-line entry point.

    python3 imessage_explorer.py            import new messages, then open the interface
    python3 imessage_explorer.py import     only import
    python3 imessage_explorer.py serve      only open the interface
    python3 imessage_explorer.py check      test access to Messages without changing anything
    python3 imessage_explorer.py search "dinner friday" --contact "Avery Stone"
    python3 imessage_explorer.py stats people
    python3 imessage_explorer.py mcp        used by AI tools; you do not run this yourself
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

from . import __version__, extract, index, mcp_server, paths, query, server, stats
from .query import Filters, QueryError


def _add_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--contact", default="", help="exact person or group name")
    parser.add_argument("--from", dest="date_from", default="", help="first day, YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", default="", help="last day, YYYY-MM-DD")
    parser.add_argument("--direction", default="", choices=["", "sent", "received"])
    parser.add_argument("--scope", default="", choices=["", "direct", "group"])
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")


def _filters(args) -> Filters:
    return Filters(contact=args.contact, date_from=args.date_from, date_to=args.date_to,
                   direction=args.direction, scope=args.scope)


def cmd_check(_args) -> int:
    """Confirm the Messages database can be read. Changes nothing."""
    print("Messages database: %s" % paths.chat_db())
    try:
        source = extract.Source(paths.chat_db())
    except extract.SourceError as exc:
        print("\nPROBLEM: %s" % exc)
        return 1
    count = source.message_count()
    source.close()
    names = extract.load_contacts(paths.addressbook_dir(), paths.contacts_override_csv())
    print("OK: can read Messages. %s messages found." % format(count, ","))
    print("Contacts: %s names found%s." % (format(len(names), ","), "" if names else " (people will show as numbers)"))
    print("Your local database will live in: %s" % paths.data_dir())
    return 0


def cmd_import(_args) -> int:
    try:
        report = index.run_import()
    except extract.SourceError as exc:
        print("\nPROBLEM: %s" % exc)
        return 1
    print(index.describe_report(report))
    return 0


def cmd_serve(args) -> int:
    # If nothing has been imported yet, or the database predates this version, the interface
    # says so and offers an Update messages button.
    return _serve(args)


def _serve(args) -> int:
    """Run the interface. If a new version was installed from inside it, start that version in its place."""
    if server.serve(port=args.port, open_browser=not args.no_browser) == "restart":
        print("\nA new version was installed. Starting it...")
        launcher = str(paths.launcher())
        os.chdir(os.path.dirname(launcher))
        # --no-browser: the page that is already open reloads itself.
        os.execv(sys.executable, [sys.executable, launcher, "serve", "--port", str(args.port), "--no-browser"])
    return 0


def cmd_probe(_args) -> int:
    """Report which optional fields this Mac's Messages database records. Reads no message text."""
    try:
        source = extract.Source(paths.chat_db())
    except extract.SourceError as exc:
        print("\nPROBLEM: %s" % exc)
        return 1
    total = source.message_count()
    print("What this Mac records, out of %s messages (no message text was read):\n" % format(total, ","))
    for entry in source.probe():
        if not entry["recorded"]:
            detail = "not recorded on this Mac"
        elif not entry["messages"]:
            detail = "recorded, but no messages have it"
        else:
            detail = "%s messages (%s%%)" % (format(entry["messages"], ","), entry["share"])
            if entry["first_seen"]:
                detail += ", first seen %s" % entry["first_seen"]
        print("  %-48s %s" % (entry["signal"], detail))
    source.close()
    return 0


def cmd_start(args) -> int:
    """The default: bring the local database up to date, then open the interface."""
    try:
        report = index.run_import()
        print(index.describe_report(report))
    except extract.SourceError as exc:
        print("\nPROBLEM: %s" % exc)
        if not index.is_ready():
            return 1
        print("\nOpening the interface with the messages imported previously.")
    return _serve(args)


def _need_index() -> bool:
    if index.is_ready():
        return True
    print("No messages imported yet. Run:  python3 imessage_explorer.py import")
    return False


def cmd_search(args) -> int:
    if not _need_index():
        return 1
    conn = index.connect(readonly=True)
    try:
        found = query.search(conn, args.query, _filters(args), limit=args.limit, sort=args.sort)
    except QueryError as exc:
        print("PROBLEM: %s" % exc)
        return 1
    finally:
        conn.close()
    if args.json:
        print(json.dumps(found, ensure_ascii=False, indent=2))
        return 0
    print("%s matching messages. Showing %s." % (format(found["total"], ","), len(found["results"])))
    for m in found["results"]:
        when = datetime.fromtimestamp(m["ts"]).strftime("%Y-%m-%d %H:%M")
        where = " [%s]" % m["counterpart"] if (m["is_group"] or m["from_me"]) else ""
        print("%s  %s%s: %s" % (when, m["sender"], where, m["text"] or "[attachment]"))
    return 0


def cmd_stats(args) -> int:
    if not _need_index():
        return 1
    conn = index.connect(readonly=True)
    try:
        filters = _filters(args)
        if args.kind == "people":
            data = stats.people(conn, filters, limit=args.limit)
        elif args.kind == "trends":
            data = stats.trends(conn, filters, args.period)
        elif args.kind == "response":
            data = stats.response(conn, filters, limit=args.limit)
        else:
            data = stats.words(conn, filters, top=args.limit)
    except QueryError as exc:
        print("PROBLEM: %s" % exc)
        return 1
    finally:
        conn.close()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(data["definition"])
    print()
    if args.kind == "people":
        t = data["totals"]
        print("%s messages (%s sent, %s received) with %s people and %s groups, %s to %s."
              % (format(t["messages"], ","), format(t["sent"], ","), format(t["received"], ","),
                 t["people"], t["groups"], t["first_day"], t["last_day"]))
        for r in data["rows"]:
            print("  %-32s %8s   sent %-8s received %s" % (r["name"][:32], format(r["total"], ","),
                                                          format(r["sent"], ","), format(r["received"], ",")))
    elif args.kind == "trends":
        for b in data["buckets"]:
            print("  %-10s sent %-8s received %s" % (b["period"], format(b["sent"], ","), format(b["received"], ",")))
    elif args.kind == "response":
        for r in data["rows"]:
            print("  %-32s my median reply %-10s theirs %-10s started by me %s, by them %s"
                  % (r["name"][:32], _duration(r["my_median_seconds"]), _duration(r["their_median_seconds"]),
                     r["started_by_me"], r["started_by_them"]))
    else:
        for label in ("words", "phrases", "emoji"):
            print("%s: %s" % (label.capitalize(), ", ".join("%s (%s)" % (x["term"], x["count"]) for x in data[label][:25])))
    return 0


def _duration(seconds) -> str:
    if seconds is None:
        return "n/a"
    if seconds < 90:
        return "%ds" % seconds
    if seconds < 5400:
        return "%dm" % round(seconds / 60.0)
    return "%.1fh" % (seconds / 3600.0)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="imessage_explorer.py",
                                     description="Search and statistics for your own Messages history.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check", help="test access to Messages; changes nothing")
    sub.add_parser("import", help="copy new messages into the local database")
    serve = sub.add_parser("serve", help="open the interface without importing")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--no-browser", action="store_true")
    search = sub.add_parser("search", help="search from the command line")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--sort", default="newest", choices=["newest", "oldest", "relevance"])
    _add_filters(search)
    stat = sub.add_parser("stats", help="statistics from the command line")
    stat.add_argument("kind", choices=["people", "trends", "response", "words"])
    stat.add_argument("--period", default="month", choices=["day", "week", "month", "year"])
    stat.add_argument("--limit", type=int, default=25)
    _add_filters(stat)
    sub.add_parser("probe", help="show which extra fields this Mac records; reads no message text")
    sub.add_parser("mcp", help="connection used by AI tools")

    args = parser.parse_args(argv)
    if args.command == "mcp":
        mcp_server.serve()
        return 0
    commands = {"check": cmd_check, "import": cmd_import, "serve": cmd_serve, "search": cmd_search, "probe": cmd_probe,
                "stats": cmd_stats, None: cmd_start}
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
