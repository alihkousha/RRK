# rrk-scraper

Scrapes the public open-data listing of [rrk.ir](https://rrk.ir) (Iran's official gazette of company notices) into a SQLite database, reads who each notice names, and lets you explore the result in a local web GUI: search, profiles, notice lists, leads, and a network map of people, companies and addresses.

The GUI is bilingual (Persian and English) and aimed at journalists. The scraper uses only the site's public page, with no login.

For how the site behaves, why the code is shaped this way, and the full run history, read the handoff brief: [src/rrk/CLAUDE.md](src/rrk/CLAUDE.md). Its section "State on 2026-11-05" says where the data stands.

## Contents

- [Setup](#setup)
- [Quick start](#quick-start)
- [Using the GUI](#using-the-gui)
- [Using the command line](#using-the-command-line)
- [How it works](#how-it-works)
- [Project layout](#project-layout)
- [Data and storage](#data-and-storage)
- [Handing over the database](#handing-over-the-database)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Handling the data safely](#handling-the-data-safely)
- [Known limits](#known-limits)

## Setup

You need Python 3.11 or newer (developed on 3.13) with SQLite FTS5, which standard builds include. The only runtime dependencies are `requests` and `urllib3`. The GUI has no build step and loads nothing from a CDN.

```bash
python3 -m venv .venv
```

```bash
. .venv/bin/activate
```

```bash
pip install -e '.[test]'
```

## Quick start

On Windows, see [Stage 3](#stage-3-run-on-windows). Run everything from `src/rrk`, because imports are top-level (`apex`, `rrs`, `parsing`, ...).

```bash
cd src/rrk
```

Start the GUI, then open <http://127.0.0.1:8765>:

```bash
python3 -m gui.server
```

Or scrape one small ad type from the command line (type 12 has 554 ads):

```bash
python3 main.py --type 12
```

rrk.ir may be reachable only from some networks, so VPN state can decide whether a run connects. A timeout usually means the network, not a block.

## Using the GUI

`python3 -m gui.server` takes `--port` (default 8765) and `--db` (default `rrk.db`). It listens on 127.0.0.1 only. Keep one server per database: two would index into the same `rrk.explore.db`.

**Explore**
- One search box for names, national codes and ids, postal codes and tracking codes, plus full-text search.
- Profiles with every link and the notices behind it.
- A notice reader that marks the people and companies named, what the notice records, capital changes and stakes.
- Lists of notices, people, companies, addresses and links, with filters, sorting, group-by and CSV export.
- A network map: expand nodes, find the shortest chain between two nodes, see what 2-5 nodes share, save as PNG.
- Leads: people on many boards, groups who keep sitting together, shared addresses.

**Runs** builds the `main.py` command from a form, streams the log, and shows a per-type table. It runs one scrape at a time, and has three modes:

| Mode | Flags | Use it for |
|---|---|---|
| List and fetch texts | none | Everything for the chosen types |
| Finish stopped types | `--resume --missing-only` | Continuing a stopped run |
| New since the last run | `--new-only` | Keeping the database up to date |

A slider limits any mode to some letter-date years (`--years`).

On its first start, and after a parser version bump, the GUI reads every stored text into `rrk.explore.db` in the background, then builds the graph. The pages work meanwhile, but search and the map show only the last finished build. With millions of texts this takes hours and many GB, so check free disk first.

## Using the command line

Common runs:

```bash
python3 main.py --type all
```

```bash
python3 main.py --type 2,3 --resume --missing-only
```

```bash
python3 main.py --type 2 --new-only
```

```bash
python3 main.py --type 9 --years 1395-1405
```

Write every stored ad with its text to `ads.json.gz`, with no requests to rrk.ir:

```bash
python3 main.py --export
```

Main options (`python3 main.py --help` lists them all):

| Flag | Default | Meaning |
|---|---|---|
| `--type` | `12` | Ad type codes, comma separated, or `all` |
| `--types-at-once` | 1 | Types scraped at the same time |
| `--sessions-per-type` | 5 | Sessions sharing one type's date windows |
| `--workers` | 12 | Detail pages fetched at once per session |
| `--detail-delay` | 0 | Seconds each worker waits between pages |
| `--page-delay` | 2 | Seconds between two grid pages |
| `--detail-backlog` | 2000 | Links a session lets wait before listing on |
| `--detail-limit` | all | Most detail pages to fetch (0: none) |
| `--missing-only` | off | List only the dates of ads without a text |
| `--resume` | off | List only the windows a stopped run left |
| `--new-only` | off | List only newspaper dates since the newest stored one |
| `--years FROM-TO` | all | Only these letter-date years (1300: no letter date) |
| `--db` | `rrk.db` | Database file |
| `--debug` | off | Log every call and save every response to `debug/<time>/` |

Defaults live in `src/rrk/config.py`.

**Load on the site.** Keep `types-at-once x sessions-per-type x workers` near 60. About 70 requests at once was fastest; 400 or more made the site drop connections and returned fewer pages. Keep grid pages at 100 rows, as the browser does. Stop a run at the first firewall or APEX error page rather than retrying it: the scraper already does this.

## How it works

The site is Oracle APEX 23.1 behind ORDS. A scrape does what a browser tab does:

1. `GET home` creates a session and its cookies.
2. The data page link, with its `cs` checksum, is taken from the home page.
3. The search is a page submit of all 51 page items, answered with a redirect.
4. The results page holds the Interactive Grid, re-signed on every load.
5. Rows come 100 at a time from `wwv_flow.ajax`.
6. Each row's detail link gives the notice's full text. Links carry a `cs` and work only in their own session, so they are never built by hand or reused.

Three facts shape the code:

- **A search returns at most 999 rows.** `scraper/windows.py` splits a capped query by letter date, newspaper date, newspaper type and city until each part fits.
- **Idle connections die silently after about 30 s,** and one grid page takes 30-40 s on the server. TCP keepalive in `ApexSession` is what makes it work.
- **The database is the checkpoint.** Any run fetches only what is missing, and stopped runs save their remaining windows in `windows_left`.

After scraping, `parsing/entities.py` reads who each text names, by national code or id, name, role and stake. `parsing/events.py` reads what the notice records. `gui/explore.py` turns that into the graph and the leads.

## Project layout

```text
rrk-scraper/
├── pyproject.toml
├── tests/                 pytest suite; labelled/ is git-ignored
└── src/rrk/
    ├── main.py            command-line entry point
    ├── config.py          defaults
    ├── CLAUDE.md          handoff brief
    ├── apex/              generic APEX client: session, state, page items, grid request, archive
    ├── rrs/               this site: page constants, RRSClient
    ├── scraper/           pipeline (sessions, detail workers), date windows
    ├── parsing/           listing rows, detail pages, entities, events
    ├── models/            Advertisement, AdDetail, issuer lines
    ├── database/          SQLite schema, repository, text packing, compaction
    └── gui/               server.py, explore.py, static/ (ES modules)
```

`diagnose.py`, `compare.py` and `smoke.py` are older debug tools, not part of a run.

## Data and storage

| File | Holds | Rebuildable |
|---|---|---|
| `rrk.db` | `ads`, `details`, `ad_types`, `windows_left`, `text_dicts` | Only by scraping again |
| `rrk.explore.db` | The GUI's mentions, notices, graph and full-text index | Yes, from `rrk.db` |
| `runs/` | Settings, log and exit code of each GUI run | No need |
| `debug/` | Raw responses from `--debug` runs | No need |

The GUI only reads `rrk.db`. `detail_code` is the unique key of an ad. A notice is its tracking code plus letter number, since one notice is printed in several issues and older ads reuse tracking codes.

Full texts are stored packed. Read them in SQL as `unzip(full_text)`, which every `database.connection` connect function registers.

To pack plain texts and shrink the file, stop the GUI and all scrapes first. It needs free disk the size of the result.

```bash
python3 -m database.compact
```

## Handing over the database

`rrk.db` is not in git. It moves through Google Drive, compressed, encrypted and split into 500 MB parts, so a dropped connection costs one part at most. Only `rrk.db` is needed: `rrk.explore.db` rebuilds from it.

The database holds real names and national codes. Send the passphrase by a different channel than the Drive link, and share the folder only with the recipient.

### Stage 1: pack and upload (sender, Linux)

Stop the GUI and any scrape first. Run these in order from `src/rrk`. It assumes `rclone` is set up with a Google Drive remote named `gdrive`, and `zstd` and `gpg` are installed.

```bash
mkdir -p ~/rrk-upload && zstd -T0 -c rrk.db | gpg --symmetric --cipher-algo AES256 --output - | split -b 500M -d - ~/rrk-upload/rrk.db.zst.gpg.part-
```

```bash
cd ~/rrk-upload && sha256sum rrk.db.zst.gpg.part-* > SHA256SUMS
```

If the upload drops, run the same command again. It skips the parts already there.

```bash
rclone copy ~/rrk-upload gdrive:rrk-handoff --drive-chunk-size 64M --transfers 2 --retries 20 --low-level-retries 50 -P
```

Confirm every part arrived. If anything differs, repeat the upload:

```bash
rclone check ~/rrk-upload gdrive:rrk-handoff
```

Then share the `rrk-handoff` folder with the recipient from the Drive website.

### Stage 2: restore (recipient, Windows, no WSL)

Keep about 35 GB free during the restore. Install the tools once, then open a new PowerShell window so they are on the `PATH`:

```powershell
winget install --id GnuPG.Gpg4win -e
```

```powershell
winget install --id Meta.Zstandard -e
```

Download every file from the Drive folder into one local folder. Drive may pack a folder download into several zip files: extract them all into the same folder. Then run these in order in PowerShell, inside that folder.

1. Check the parts. Every line must say `OK`; download any `FAILED` part again.

```powershell
Get-Content SHA256SUMS | ForEach-Object { $hash, $file = $_ -split '\s+', 2; $file = $file.TrimStart('*'); if ((Get-FileHash $file -Algorithm SHA256).Hash -ieq $hash) { "OK $file" } else { "FAILED $file" } }
```

2. Join the parts. Use `cmd`, not a PowerShell pipe: PowerShell pipes can corrupt binary data.

```powershell
cmd /c "copy /b rrk.db.zst.gpg.part-* rrk.db.zst.gpg"
```

3. Decrypt. A window asks for the passphrase. A wrong join or a damaged file fails here rather than giving a bad database.

```powershell
gpg --output rrk.db.zst --decrypt rrk.db.zst.gpg
```

4. Decompress:

```powershell
zstd -d rrk.db.zst -o rrk.db
```

5. Move `rrk.db` into `src\rrk`, then delete the parts, `rrk.db.zst.gpg` and `rrk.db.zst`.

### Stage 3: run on Windows

Install Python 3.11 or newer from python.org, then set up from the repo root. On Windows the command is `python`, not `python3`.

```powershell
python -m venv .venv
```

```powershell
.venv\Scripts\python -m pip install -e ".[test]"
```

Start the GUI from `src\rrk`, then open <http://127.0.0.1:8765>:

```powershell
cd src\rrk
```

```powershell
..\..\.venv\Scripts\python -m gui.server
```

The first start reads every text and builds the graph. Expect hours, and more than 15 GB of extra disk for `rrk.explore.db`. The pages work meanwhile.

The project was developed and tested on Linux only, so Windows is untested. From reading the code:

- **Explore** should work.
- **The Runs page will not work.** It starts scrapes through `sh` and stops them with `os.killpg`, neither of which Windows has.
- **Scrapes** should run from the command line instead, such as `..\..\.venv\Scripts\python main.py --type 2,3 --resume --missing-only`. Press Ctrl+C to stop one.

## Testing

From the repo root:

```bash
python3 -m pytest
```

Tests that need a browser capture or the labelled set skip when those files are absent.

The parser has its own measure over 200 labelled ads. It prints precision and recall, and pytest fails below `FLOORS`:

```bash
python3 tests/test_labelled.py
```

After changing the reading in `parsing/entities.py` or `parsing/events.py`, bump `entities.VERSION`. The GUI then re-reads every text.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Timeouts or no connection to rrk.ir | Network or VPN state. Try another route before assuming a block |
| `RemoteDisconnected` bursts | Too many requests at once. Lower sessions or workers |
| `DetailPageError`, run stops | A firewall or APEX error page. Don't retry at once; the `<title>` is in the message |
| `database or disk is full` | Free disk, then continue with "Finish stopped types" |
| A change to the graph or leads doesn't show | It needs a rebuild. With no new texts, run `DELETE FROM g_meta` in `rrk.explore.db` |
| Search or map looks behind | They show the last finished graph build, at most about 10 minutes behind the texts |

## Handling the data safely

Never commit or share these. All are in `.gitignore`.

- `*.har`: hold live session cookies.
- `*.html`, `*.log`, `*.zip`, `debug/`, `runs/`: hold session ids and checksums, and may hold copies of the above.
- `rrk.db`, `rrk.explore.db`, `ads.json*`, `tests/labelled/`: hold real people's names and national codes.

`--verify-ssl` is off by default until a run shows that the site's certificate chain verifies.

## Known limits

- Names and roles are read by heuristics, so check a notice before relying on a link. National codes and ids are the identity; names are labels.
- A name spelled differently in the role sentence gets no role. A person named without a code in two companies becomes two nodes.
- Rows on a capped day without a newspaper date can be unreachable.
- Windows is untested, and the GUI's Runs page needs Linux or macOS.
- The network map is not checked past a few hundred nodes.
- `--new-only` and the split by newspaper type and city are built and tested, but not yet run live.
