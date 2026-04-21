import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from . import scraper
from .dbmanager.database_manager import DatabaseManager
from .filestore import FileStore
from .remote_filestore import HttpApiStore  # noqa: F401 — registers "http-api"


def _make_filestore(root: str) -> FileStore:
    store_name = os.environ.get("FILESTORE", "local")
    store_cls = FileStore._REGISTRY.get(store_name)
    if store_cls is None:
        available = ", ".join(FileStore._REGISTRY)
        raise ValueError(
            f"Unknown FILESTORE '{store_name}'. Available: {available}"
        )
    return store_cls.from_env(root)


def parse_args():
    parser = argparse.ArgumentParser(
        description="CLI with config file and fixed choices"
    )
    parser.add_argument(
        "--env", type=Path, default="./.env", help="Path to the .env file"
    )

    args, _ = parser.parse_known_args()

    # Load .env early so NEODYMIUM_PLUGINS is available before registry() is called
    load_dotenv(args.env)
    modules = [Path(p) for p in os.environ.get("NEODYMIUM_PLUGINS", "").split(":") if p]

    choices = ["all"] + [s.__name__ for s in scraper.Scraper.registry(modules)]

    parser.add_argument(
        "scraper", choices=choices, help=f"Pick from: {', '.join(choices)}", nargs="+"
    )
    parser.add_argument(
        "--output", type=Path, default=Path("./output"), help="FileStore root directory"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape metadata without downloading files",
    )

    return parser.parse_args(), modules


def main():
    args, modules = parse_args()

    dm = DatabaseManager(
        username=os.environ["DB_USERNAME"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
    )

    fs = _make_filestore(str(args.output))
    print(f"Using filestore: {type(fs).__name__} at {args.output}")

    print(f"Chosen Scrapers: {args.scraper}")
    if "all" in args.scraper:
        if len(args.scraper) > 1:
            print("'all' and other scrapers specified. Using all scrapers.")
        scrapers = [s(dm, fs) for s in scraper.Scraper.registry(modules)]
    else:
        scrapers = [
            s(dm, fs)
            for s in scraper.Scraper.registry(modules)
            if s.__name__ in args.scraper
        ]

    curr_scrapers = {s: s.run(dry_run=args.dry_run) for s in scrapers}
    stopped = False
    finished = list()
    errored = dict()
    while len(curr_scrapers) > 0 and not stopped:
        to_remove = []
        for instance, scrape in curr_scrapers.items():
            try:
                next(scrape)
            except StopIteration:
                print(f"{instance.__class__.__name__} Has Finished")
                finished.append(instance)
                to_remove.append(instance)
            except scraper.UnhealthyScraper as e:
                print(
                    f"{instance.__class__.__name__} is unhealthy and is disabled this run: {e}"
                )
                errored[instance] = e
                to_remove.append(instance)
            except KeyboardInterrupt:
                stopped = True
                break

        for instance in to_remove:
            del curr_scrapers[instance]

    if len(errored) > 0 or stopped:
        if stopped:
            print("Scrapers stopped by user")

        print(f"{len(finished)} scrapers completed running")
        print(f"{len(errored)} scrapers errored out")
    else:
        print("Successfully completed running all scrapers")


if __name__ == "__main__":
    main()
