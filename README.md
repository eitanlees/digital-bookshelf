# digital-bookshelf

Turn your reading log into a **digital bookshelf**: one image with the cover of
every book you read, arranged in a neat grid.

It works with a **StoryGraph** or **Goodreads** export, or with **any CSV of
books you keep yourself** (a Notion database, a Google Sheet, an Excel file, …).
It finds each book's cover online and lays them all out for you. All you need is
titles and authors; ISBNs help but aren't required.

```bash
bookshelf render my-books.csv --year 2025 --title "My {year} in Books" --subtitle "{count} books"
```

## Install

```bash
pipx install digital-bookshelf      # or: uv tool install digital-bookshelf
```

Requires Python 3.10+. The only dependency is [Pillow](https://python-pillow.org).

## Your reading log

`bookshelf` reads three kinds of CSV files and works out which one you gave it
from the header row.

### StoryGraph

Click your profile picture → **Manage Account** → **Export StoryGraph Library**.
Use the CSV file as is.

### Goodreads

**My Books** → **Import and export** (under *Tools*) → **Export Library**. Use
the CSV file as is.

### Any other CSV

If you track your reading somewhere else, export or save it as a CSV. The only
column you need is the book's title; everything else is optional but helps:

```csv
Title,Author,Status,Date Finished
Dune,Frank Herbert,Read,2025-01-14
The Remains of the Day,Kazuo Ishiguro,Read,"March 3, 2025"
Middlemarch,George Eliot,Currently Reading,
```

Columns are matched by name, ignoring case. The first matching column that has a
value is used:

| Meaning     | Column names                                                                         |
| ----------- | ------------------------------------------------------------------------------------ |
| Title       | `Title`, `Name`, `Book`, `Book Title`, `Book Name` (**required**)                    |
| Author      | `Author`, `Authors`, `Author(s)`, `Author Name`, `Writer`, `By`                      |
| Status      | `Status`, `Read Status`, `Reading Status`, `State`, `Shelf`                          |
| Finish date | `Date Finished`, `Finished`, `Finish`, `Finish Date`, `Date Read`, `Read Date`, `End Date`, `Date`, … |
| Year read   | `Year Read`, `Year Finished`, `Read In`, used if there is no full date               |
| ISBN        | `ISBN`, `ISBN13`, `ISBN-13`, `ISBN10`, `ISBN-10`                                      |

- **Status:** values like *Read*, *Finished*, *Currently Reading*, *Want to
  Read*, *TBR*, and *Stopped*/*DNF* are understood. If there is no status column
  at all, every row is treated as a book you've read.
- **Dates:** `2025-12-23`, `2025/12/23`, `12/23/2025`, `December 23, 2025`, and
  ranges like `May 2, 2025 → May 9, 2025` all work. For a range, the end date
  counts as when you finished.
- **Several authors:** separate them with commas.

Other columns (rating, tags, page count, …) are ignored, so you don't need to
tidy up your spreadsheet first.

**Notion:** open the database → `•••` → **Export** → *Markdown & CSV*. Unzip it
and use the `.csv` file. Use the `_all.csv` one if your view is filtered.

**Google Sheets / Excel / Numbers / Airtable:** use *Download* or *Export* as
CSV.

To skip auto-detection, pass `--format storygraph`, `--format goodreads`, or
`--format csv`.

## Usage

```bash
# Preview which books will be included
bookshelf list export.csv --year 2025

# Make the image (writes bookshelf-2025.png)
bookshelf render export.csv --year 2025

# Books standing on wooden shelves, with a heading
bookshelf render export.csv -y 2025 --style shelf --title "{year}" --subtitle "{count} books"
```

By default only books marked as **read** are included, ordered by the date you
finished them. Common options:

| Option                          | What it does                                                                     |
| ------------------------------- | -------------------------------------------------------------------------------- |
| `-y, --year YEAR`               | Only books finished that year. Re-reads count in every year you finished them.   |
| `--status STATUS`               | `read` (default), `reading`, `to-read`, `dnf`, `other`, or `all`. Repeatable.     |
| `--sort finished\|title\|author\|none` | Cover order. Add `--reverse` to flip it.                                  |
| `-o, --output FILE`             | `.png`, `.jpg`, or `.webp`.                                                      |
| `--title`, `--subtitle`         | Heading text. `{year}` and `{count}` are filled in for you.                      |
| `--columns N`                   | Covers per row (default: whatever makes the image roughly square).               |
| `--style grid\|shelf`           | Plain grid, or books on shelves.                                                 |
| `--fit contain\|fill`           | Show whole covers (default), or crop them all to the same size.                  |
| `--cover-width PX`              | Size of each cover (default 300).                                                |
| `--background COLOR`            | Any CSS-style colour, e.g. `white`, `#1d1d1f`.                                   |
| `--font FILE.ttf`               | Font for the heading and placeholder covers.                                     |

You can pass several exports at once (for example a Goodreads history plus a
newer StoryGraph export). Duplicate books are shown once.

Run `bookshelf render --help` for everything.

## Where covers come from

Covers are searched in this order:

1. **Open Library by ISBN** (when the export has one). This is the exact edition
   you logged.
2. **Apple Books** search by title and author. High resolution, usually the
   current edition.
3. **Open Library** search by title and author.
4. **Google Books**, only if you set a `GOOGLE_BOOKS_API_KEY` environment
   variable. Its anonymous quota is shared and almost always exhausted.

Change the order or drop sources with `--sources openlibrary,apple`.

Downloaded covers are cached in `~/.cache/digital-bookshelf` (or
`$XDG_CACHE_HOME/digital-bookshelf`), so re-rendering with different layout
options is instant. Pass `--refresh` to search again.

Books with no cover found get a simple placeholder cover with the title and
author on it.

## Fixing or choosing covers yourself

Sometimes the search finds the wrong edition, or a cover you don't like. Every
book has a *slug* (shown by `bookshelf list`), such as
`the-wager-david-grann`. Put an image with that name (or the book's ISBN) in a
folder and pass `--covers`. Your image always wins:

```bash
bookshelf covers export.csv -y 2025 -o covers/   # download every cover into covers/
# ...look through covers/, replace any you don't like, add missing ones...
bookshelf render export.csv -y 2025 --covers covers/
```

## Python API

```python
from digital_bookshelf import CoverFinder, RenderOptions, filter_books, load_books, render_bookshelf

books = filter_books(load_books("storygraph-export.csv"), year=2025)
covers = CoverFinder().find_all(books)
image = render_bookshelf(covers, RenderOptions(style="shelf", title="2025"))
image.save("bookshelf.png")
```

## Development

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

The tests never touch the network.

### Releasing to PyPI

Releases are published by GitHub Actions using
[trusted publishing](https://docs.pypi.org/trusted-publishers/). There are no
API tokens to manage.

1. One time: on PyPI, add a *pending publisher* for project `digital-bookshelf`
   with owner `eitanlees`, repository `digital-bookshelf`, workflow
   `release.yml`, environment `pypi`.
2. Bump `version` in `pyproject.toml`, commit, then create a GitHub release
   tagged `vX.Y.Z`. The workflow builds and uploads the package.

## License

MIT
