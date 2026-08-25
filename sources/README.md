# VLL Corpus Sources

This directory contains the reproducible external-corpus acquisition and cleanup pipeline used by `vll_organism` research.

The generated data directories are disposable:

```text
corpus_sources.tsv
        |
        v
build_corpus.sh
        |
        +--> corpus/
        |      raw verified upstream sources
        |
        +--> corpus_harvest/
        |      cleaned content suitable for VLL ingestion
        |
        +--> corpus_source_receipts.tsv
        |      provenance/integrity receipts for raw sources
        |
        +--> corpus_harvest_report.tsv
               audit of every harvest keep/reject decision
```

`corpus/` is the reproducible raw-source cache.

`corpus_harvest/` is generated output. It may be deleted at any time; the next run reconstructs it from `corpus/`.

Do not feed `corpus/` directly into VLL.

## Requirements

Required:

```text
bash
git
python3
sha256sum
```

For online acquisition, one of:

```text
curl
wget
```

No third-party Python packages are required.

## Quick start

From `vll_organism/sources/`:

```bash
chmod +x build_corpus.sh
./build_corpus.sh
```

For an offline integrity/harvest replay using already-downloaded raw sources:

```bash
OFFLINE=1 ./build_corpus.sh
```

## Source manifest

`corpus_sources.tsv` is tab-separated:

```text
type<TAB>name<TAB>url
```

Supported types:

```text
git
file
```

Example:

```text
git	open_music_theory	https://github.com/openmusictheory/openmusictheory.github.io.git
file	rfc9293_tcp.txt	https://www.rfc-editor.org/rfc/rfc9293.txt
```

Blank lines and lines beginning with `#` are ignored.

Add sources to the manifest. The builder does not need to be edited when the list grows.

## Raw-source verification

Every online run verifies the raw source layer before harvesting.

### Standalone files

Standalone URLs are downloaded to a temporary file on every online run.

The temporary download is compared against:

- the current local copy;
- the previous source receipt.

This detects:

```text
missing local source
local corruption/modification
upstream content change
manifest URL change
```

A verified or repaired file is then recorded in `corpus_source_receipts.tsv`.

### Git repositories

Existing Git sources are treated as generated caches.

Before updating, the script:

1. verifies the configured origin URL;
2. runs Git object-integrity checks;
3. restores tracked files to `HEAD`;
4. removes untracked generated debris;
5. performs a shallow fast-forward update.

If the source cache is corrupt, it is recloned from the manifest URL.

The receipt records:

- resolved `HEAD` commit;
- SHA-256 of `git archive HEAD`;
- byte count of that archive stream.

This gives each repository both a Git-native revision identity and a content fingerprint.

## `corpus_source_receipts.tsv`

Columns:

```text
type
name
url
resolved_revision
sha256
bytes
verified_at_utc
action
```

Typical actions include:

```text
DOWNLOADED
CLONED
VERIFIED
UPSTREAM_CHANGED
REPAIRED_LOCAL
RECLONED
SOURCE_URL_CHANGED
OFFLINE_VERIFIED
```

The receipt is the provenance/integrity record for the raw corpus used to construct the current harvest.

For publication-grade experiments, preserve this file with the experimental results.

## Clean harvest

`corpus_harvest/` is rebuilt from scratch on every run.

This is intentional. Missing, stale, manually edited, or partially deleted harvest files cannot become authoritative. The builder removes the old harvest and regenerates it from the verified raw corpus.

The cleaner accepts content-bearing sources such as:

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

### OpenStax routing and textbook modules

OpenStax bundle repositories use their XML metadata as an authoritative table of contents.

The builder follows this chain:

```text
META-INF/books.xml
        ->
collections/<book>.collection.xml
        ->
<col:module document="m#####">
        ->
modules/m#####/index.cnxml
```

The routing files are **control data**, not VLL corpus content.

The builder therefore:

1. parses `META-INF/books.xml`;
2. follows every declared collection `href`;
3. extracts every referenced module ID;
4. verifies that `modules/<id>/index.cnxml` exists locally;
5. admits only those referenced module bodies;
6. excludes `META-INF/`, `collections/`, media, covers, and unreferenced/orphan modules from `corpus_harvest/`.

A referenced module that is missing from the cloned repository is a hard build failure. The script does not silently produce an incomplete textbook.

The Git clone already retrieves the module tree. The collection manifests determine **which modules constitute the declared books**.


### Repository/site machinery excluded

Common excluded directories include:

```text
.git/
.github/
.gitlab/
META-INF/
node_modules/
vendor/
target/
dist/
build/
__pycache__/
.venv/
venv/
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
404*
README*
LICENSE*
LICENCE*
COPYING*
CONTRIBUTING*
CODE_OF_CONDUCT*
SECURITY*
CHANGELOG*
CHANGES*
AUTHORS*
CONTRIBUTORS*
CITATION*
mimetype
```

These are rejected because being textual is not sufficient for semantic-corpus admission.

### Content cleanup

Markdown:

- YAML frontmatter is removed;
- HTML comments are removed;
- Jekyll/Liquid directives are removed;
- Markdown URLs are stripped while retaining visible link text;
- short navigation/link indexes are rejected.

HTML/XHTML:

- visible text is extracted;
- script/style/SVG/navigation/header/footer content is suppressed.

XML/CNXML:

- element text is extracted in document order;
- malformed XML falls back to conservative tag removal.

TeX/LaTeX:

- comments are removed;
- equations and formal commands are retained.

Grammar formats:

- `.gram`, `.grammar`, `.ebnf`, `.bnf`, and `.peg` are treated as structural artifacts rather than prose.

Exact duplicate cleaned content is admitted only once to prevent repository duplication from artificially weighting retrieval.

## Harvest admission thresholds

Normal prose-like material must contain at least:

```text
300 cleaned characters
45 prose words
180 alphabetic characters
```

Grammar artifacts use a separate minimum:

```text
120 cleaned characters
```

The objective is not maximum file count.

The objective is a corpus containing enough semantic, procedural, mathematical, grammatical, or relational structure to be useful to VLL.

## `corpus_harvest_report.tsv`

Every raw file receives a KEEP or REJECT decision.

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

Examples:

```text
REJECT  open_music_theory/404.md
        excluded repository/site file: 404.md

REJECT  openstax_biology/META-INF/container.xml
        excluded directory: META-INF

KEEP    ...
        content-bearing source
```

If a useful file is rejected, improve the general cleanup rule rather than manually copying it into `corpus_harvest/`.

If junk survives, add a general exclusion/content rule rather than deleting the generated output by hand.

That keeps corpus construction reproducible.

## Preserve source boundaries

Do not concatenate the harvest into one giant file before VLL ingestion.

Relative paths preserve provenance and allow experiments to measure:

- cross-document edges;
- source-to-source transport;
- bridge chunks;
- source routing matrices;
- same-source versus cross-source activation.

## Do not encode benchmark labels

The organism should not be told which documents are intended structural analogues.

Avoid metadata such as:

```text
problem_17 -> solution_17
analogue_of = ...
same_relation = true
```

Those labels belong to the evaluator.

## Why the corpus is heterogeneous

The supplied manifest intentionally spans unrelated domains:

- TCP, SMTP, DNS;
- formal logic;
- parser/compiler grammar;
- music theory;
- Git history/provenance;
- chemistry;
- biology;
- physics;
- engineering;
- narrative prose.

For VLL experiments, this allows topical similarity to compete against relations such as:

```text
missing element
wrong ordering
wrong attachment
causal ancestry
closure
state transition
dependency structure
cross-domain analogy
```

A homogeneous corpus would make those experiments much less discriminating.

## Git policy

Commit the source pipeline:

```text
sources/README.md
sources/build_corpus.sh
sources/corpus_sources.tsv
```

Normally do not commit generated upstream content:

```gitignore
sources/corpus/
sources/corpus_harvest/
sources/corpus_source_receipts.tsv
sources/corpus_harvest_report.tsv
```

For a published experiment, preserve the receipts/report with the experimental artifact even if they are not kept in the main Git history.

Each upstream source remains governed by its own license and terms.

## Custom paths

```bash
./build_corpus.sh another_manifest.tsv
```

or:

```bash
CORPUS_DIR=/tmp/corpus \
HARVEST_DIR=/tmp/harvest \
SOURCE_RECEIPTS=/tmp/source_receipts.tsv \
HARVEST_REPORT=/tmp/harvest_report.tsv \
./build_corpus.sh
```

## Authority chain

The intended authority chain is:

```text
manifest + upstream source
        ->
verified corpus/
        ->
cleaning rules
        ->
generated corpus_harvest/
```

`corpus_harvest/` is never authoritative.

The raw source receipts make missing, corrupted, changed, or updated inputs visible before the VLL corpus is rebuilt.
