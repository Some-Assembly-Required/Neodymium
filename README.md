# Neodymium

## Setup
Install pip dependencies. Currently this includes development dependencies as well:

`$ pip install -r requirements.txt`

## First Time Setup for Development
Make sure pre-commit hooks are enabled and installed: 

`$ pre-commit install`

## Plugins

Custom scrapers outside the `neodymium/scrapers/` directory can be loaded by setting `NEODYMIUM_PLUGINS` in your `.env` file. The value is a `:` separated list of paths to Python files or packages:

```
NEODYMIUM_PLUGINS=/path/to/my_scraper.py:/path/to/scraper_package
```

Each entry can be:
- A single `.py` file containing one or more `Scraper` subclasses
- A directory (package) with an `__init__.py` — all submodules are loaded automatically

Once loaded, the scrapers register themselves and appear in the CLI like any built-in scraper.

