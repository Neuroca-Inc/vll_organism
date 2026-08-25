# VLL Corpus Sources

This directory contains the reproducible source-ingestion pipeline used to build external text corpora for `vll_organism` research.

The pipeline separates three things deliberately:

```text
corpus_sources.tsv
        |
        v
build_corpus.sh
        |
        +--> corpus/                 raw downloaded sources
        |
        +--> corpus_harvest/         cleaned text suitable for VLL ingestion
        |
        +--> corpus_harvest_report.tsv
                                     audit of every keep/reject decision
```

`corpus/` is the raw source cache. `corpus_harvest/` is the curated semantic corpus.

Do not feed `corpus/` directly into VLL.

## Why this exists

Text repositories contain a lot of material that is technically text but is poor knowledge-corpus material:

- `404.md`
- READMEs and contribution instructions
- licenses and changelogs
- site templates
- navigation indexes
- build metadata
- configuration files
- JavaScript/CSS/assets
- empty frontmatter pages
- generated or malformed markup

The corpus builder downloads source material first, then applies a conservative cleanup pass so VLL receives content-bearing prose, formal material, grammar definitions, and other structurally useful text rather than repository/site machinery.

The cleanup is intentionally auditable. Nothing is silently discarded without a row in `corpus_harvest_report.tsv`.

---

## Files

### `corpus_sources.tsv`

Extensible source manifest.

Schema:

```text
type<TAB>name<TAB>url
```

Supported source types:

- `git` — shallow-clone a Git repository
- `file` — download one file over HTTP(S)

Example:

```text
git	open_music_theory	https://github.com/openmusictheory/openmusictheory.github.io.git
file	rfc9293_tcp.txt	https://www.rfc-editor.org/rfc/rfc9293.txt
```

Lines beginning with `#` and blank lines are ignored.

Add new sources to this file. The harvest script does not need to be modified when the manifest grows.

### `build_corpus.sh`

Downloads or updates every source in the manifest and rebuilds the curated harvest.

Default outputs:

```text
corpus/
corpus_harvest/
corpus_harvest_report.tsv
```

---

## Requirements

Required:

```text
bash
git
python3
```

One of:

```text
curl
wget
```

No third-party Python packages are required.

---

## Quick start

From the repository:

```bash
cd sources
chmod +x build_corpus.sh
./build_corpus.sh
```

The script processes each manifest entry sequentially.

For `git` entries:

- missing repositories are shallow-cloned with `--depth 1`;
- existing Git checkouts are updated with `git pull --ff-only`;
- a non-Git path occupying the requested destination is treated as an error.

For `file` entries:

- non-empty existing files are reused;
- missing files are downloaded;
- partial downloads use a `.part` file and are renamed only after success;
- downloads retry transient failures.

After acquisition finishes, `corpus_harvest/` is rebuilt from scratch from the current contents of `corpus/`.

---

## What gets harvested

The cleanup pass currently accepts content-bearing files with these extensions:

```text
.txt
.md
.markdown
.mdx
.rst
.asc
.adoc
.asciidoc
.tex
.latex
.xml
.cnxml
.xhtml
.html
.htm
.gram
.grammar
.ebnf
.bnf
.peg
```

This list is deliberately narrower than "all text files."

Formats such as JSON, YAML, TOML, lockfiles, configuration files, source code, CSS, and JavaScript are not admitted merely because they are readable text.

### Markdown

The cleaner removes:

- YAML frontmatter
- HTML comments
- Jekyll/Liquid block tags
- Jekyll/Liquid expression tags

It also rejects short Markdown files that are primarily navigation/link indexes.

### HTML / XHTML

The cleaner extracts visible text and suppresses content inside common non-semantic containers such as:

```text
script
style
svg
nav
footer
header
```

The harvested output receives an additional `.txt` suffix to avoid collisions and make the transformation explicit.

Example:

```text
chapter.html
    ->
chapter.html.txt
```

### XML / CNXML

XML and OpenStax CNXML are parsed and reduced to textual content while preserving element text order.

Malformed XML falls back to tag removal rather than being automatically accepted as raw markup.

Harvested XML/CNXML output also receives a `.txt` suffix.

### TeX / LaTeX

TeX comments are removed.

Equations and TeX commands are retained because mathematical and formal structure can be useful to VLL.

### Grammar files

Files such as:

```text
.gram
.grammar
.ebnf
.bnf
.peg
```

are treated as structural artifacts rather than prose. They use a separate minimum-size rule instead of the normal prose-word threshold.

---

## Automatic rejection rules

Repository and site machinery is excluded before semantic cleanup.

Common excluded directories include:

```text
.git/
.github/
.gitlab/
.idea/
.vscode/
node_modules/
vendor/
target/
dist/
build/
__pycache__/
.venv/
venv/
.tox/
.pytest_cache/
coverage/
assets/
images/
img/
fonts/
css/
javascript/
js/
_layouts/
_sass/
```

Common excluded files include:

```text
404.md
404.html
README*
LICENSE*
LICENCE*
COPYING*
CONTRIBUTING*
CODE_OF_CONDUCT*
SECURITY.md
CHANGELOG*
CHANGES.md
AUTHORS*
CONTRIBUTORS*
CITATION*
```

A file that survives path filtering can still be rejected after cleanup.

Normal prose-like sources must contain at least:

```text
300 cleaned characters
45 prose words
180 alphabetic characters
```

Grammar artifacts must contain at least:

```text
120 cleaned characters
```

The cleaner also rejects:

- binary/NUL-containing files
- files that are not valid UTF-8
- very small cleaned artifacts
- short Markdown navigation/link indexes

The goal is not to maximize file count. It is to keep sources that contain enough semantic or relational structure to be useful to the organism.

---

## Audit report

Every run writes:

```text
corpus_harvest_report.tsv
```

Columns:

```text
status
source_path
harvest_path
reason
bytes_in
bytes_out
words
```

Example:

```text
REJECT	open_music_theory/404.md		excluded repository/site file: 404.md	...
KEEP	open_music_theory/romanNumeralIntroduction.md	open_music_theory/romanNumeralIntroduction.md	content-bearing source	...
```

Use this report when tuning the cleanup rules.

If a useful source is rejected, prefer improving a general rule in `build_corpus.sh` rather than manually copying the file into the harvest.

Likewise, if junk survives, add a general exclusion or content test instead of deleting the harvested copy by hand.

That keeps corpus construction reproducible.

---

## Adding a source

Add one row to `corpus_sources.tsv`.

### Repository

```text
git	my_source	https://github.com/example/project.git
```

The repository will appear under:

```text
corpus/my_source/
```

Accepted text within it will be mirrored under:

```text
corpus_harvest/my_source/
```

### Individual file

```text
file	my_reference.txt	https://example.org/reference.txt
```

It will appear as:

```text
corpus/my_reference.txt
corpus_harvest/my_reference.txt
```

if it passes cleanup.

Then rerun:

```bash
./build_corpus.sh
```

No script edit is required.

---

## Custom paths

The manifest can be supplied as the first argument:

```bash
./build_corpus.sh my_sources.tsv
```

Output locations can be overridden with environment variables:

```bash
CORPUS_DIR=/tmp/vll-corpus \
HARVEST_DIR=/tmp/vll-harvest \
HARVEST_REPORT=/tmp/vll-harvest-report.tsv \
./build_corpus.sh corpus_sources.tsv
```

---

## Current source mix

The supplied manifest intentionally spans unrelated domains so VLL experiments can distinguish topical similarity from relational or structural similarity.

It currently includes material from:

- TCP, SMTP, and DNS protocol specifications
- CPython parser/compiler documentation and PEG grammar
- formal logic
- music theory
- Rust language semantics
- Git history/provenance
- chemistry
- biology
- university physics
- historical engineering texts
- general science writing
- narrative fiction

This diversity is deliberate.

For experiments involving missing elements, wrong order, wrong attachment, causal ancestry, closure, state transitions, dependency structure, or cross-domain analogy, a heterogeneous corpus is more informative than a collection of documents all written about the same research vocabulary.

---

## Corpus-use guidance

### Preserve source boundaries

Do not concatenate the harvest into one giant file before ingestion.

The relative paths retained in `corpus_harvest/` provide useful source provenance and allow downstream experiments to measure:

- cross-document edges
- source-to-source transport
- bridge chunks
- source routing matrices
- same-source versus cross-source activation

### Do not encode benchmark labels into the corpus

For structural-retrieval experiments, the organism should not be told that two documents are intended analogues.

Avoid adding metadata such as:

```text
problem_17 -> solution_17
same_relation = true
analogue_of = ...
```

Those labels belong to the evaluator, not the organism.

### Keep raw and curated data separate

`corpus/` exists so source acquisition is reproducible and cleanup decisions can be revisited.

`corpus_harvest/` is disposable and deterministic: every run recreates it from `corpus/`.

---

## Git repository policy

The scripts and manifest are intended to be committed.

The downloaded corpora normally should not be.

Recommended `.gitignore` entries:

```gitignore
sources/corpus/
sources/corpus_harvest/
sources/corpus_harvest_report.tsv
```

This avoids:

- bloating the repository;
- duplicating upstream projects;
- accidentally redistributing material under incompatible terms;
- committing generated harvest output.

Users can reproduce the local corpus by running:

```bash
cd sources
./build_corpus.sh
```

Each upstream source remains governed by its own license and terms.

---

## Reproducibility boundary

The manifest fixes **where source material comes from**, but most Git entries currently follow the upstream repository's latest default branch because they are shallow-cloned without pinning a commit.

Therefore two runs performed at different dates may not produce byte-identical corpora.

For publication-grade experiments, record at least:

- the date of acquisition;
- Git commit hashes for repository sources;
- checksums for downloaded standalone files;
- the resulting `corpus_harvest_report.tsv`.

A future manifest revision can add optional pinned revisions/checksums if exact corpus reconstruction becomes an experimental requirement.

---

## Design principle

The source pipeline follows one simple rule:

> **Raw text availability is not sufficient for corpus admission.**

The raw layer preserves upstream material.

The harvest layer admits content that has enough semantic, formal, procedural, or relational structure to be useful to VLL.

The audit report preserves the boundary between them.
