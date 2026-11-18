#!/usr/bin/env python3
"""Start VacancyWatch.

    python run.py              # start the app and open it in your browser
    python run.py --lan        # also reachable from your phone on the same Wi-Fi
    python run.py --scan-once  # run one check and exit (for Task Scheduler / cron)
"""
import argparse
import logging
import socket
import threading
import webbrowser

from vacancywatch import db, scanner, scheduler, sources, web


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "your-computer-ip"


def main():
    ap = argparse.ArgumentParser(description="VacancyWatch - Assistant Professor vacancy tracker")
    ap.add_argument("--port", type=int, default=5050)
    ap.add_argument("--lan", action="store_true", help="allow access from other devices on your network")
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")
    ap.add_argument("--scan-once", action="store_true", help="run a single check, send alerts, and exit")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db.init()
    s = db.all_settings()
    if s.get("profession"):
        # after an update: pick up new institutions from the list and re-check subjects of saved vacancies
        sources.sync(s.get("states") or [], s.get("include_national"), s.get("include_colleges"))
        scanner.reevaluate_subjects()
        merged = scanner.dedupe_existing()
        if merged:
            print(f"  Cleaned up history: merged {merged} duplicate notices.")

    if args.scan_once:
        if not db.get_setting("profession"):
            print("Please run the app once without --scan-once to complete setup first.")
            return
        result = scanner.run_scan()
        print("Check finished:", result)
        import time
        time.sleep(5)  # give alert sending a moment to finish
        return

    scheduler.start()
    app = web.create_app()
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    url = f"http://127.0.0.1:{args.port}"
    print("\n  VacancyWatch is running.")
    print(f"  Open {url} in your browser.")
    if args.lan:
        print(f"  On your phone (same Wi-Fi): http://{lan_ip()}:{args.port}")
    print("  Keep this window open - it checks for new vacancies in the background.\n")
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    try:
        from waitress import serve
        serve(app, host=host, port=args.port, threads=8)
    except ImportError:
        app.run(host=host, port=args.port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
