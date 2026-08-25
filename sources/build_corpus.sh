#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_VERSION="2026-08-25-v4-source-aware"
echo "VLL corpus builder: $SCRIPT_VERSION"

MANIFEST="${1:-corpus_sources.tsv}"
CORPUS_DIR="${CORPUS_DIR:-corpus}"
HARVEST_DIR="${HARVEST_DIR:-corpus_harvest}"
SOURCE_RECEIPTS="${SOURCE_RECEIPTS:-corpus_source_receipts.tsv}"
HARVEST_REPORT="${HARVEST_REPORT:-corpus_harvest_report.tsv}"
OFFLINE="${OFFLINE:-0}"

if [[ ! -f "$MANIFEST" ]]; then
    echo "ERROR: manifest not found: $MANIFEST" >&2
    exit 1
fi

for cmd in git python3 sha256sum; do
    command -v "$cmd" >/dev/null 2>&1 || {
        echo "ERROR: required command not found: $cmd" >&2
        exit 1
    }
done

if [[ "$OFFLINE" != "1" ]]; then
    if command -v curl >/dev/null 2>&1; then
        DOWNLOADER="curl"
    elif command -v wget >/dev/null 2>&1; then
        DOWNLOADER="wget"
    else
        echo "ERROR: curl or wget is required unless OFFLINE=1" >&2
        exit 1
    fi
fi

mkdir -p "$CORPUS_DIR"

PREVIOUS_RECEIPTS="$(mktemp)"
RECEIPT_TMP="$(mktemp)"
cleanup() {
    rm -f "$PREVIOUS_RECEIPTS" "$RECEIPT_TMP"
}
trap cleanup EXIT

if [[ -f "$SOURCE_RECEIPTS" ]]; then
    cp "$SOURCE_RECEIPTS" "$PREVIOUS_RECEIPTS"
else
    : > "$PREVIOUS_RECEIPTS"
fi

printf 'type\tname\turl\tresolved_revision\tsha256\tbytes\tverified_at_utc\taction\n' > "$RECEIPT_TMP"

utc_now() {
    date -u '+%Y-%m-%dT%H:%M:%SZ'
}

previous_field() {
    local name="$1"
    local field="$2"
    awk -F '\t' -v wanted="$name" -v field="$field" 'NR > 1 && $2 == wanted { print $field; exit }' "$PREVIOUS_RECEIPTS"
}

hash_file() {
    sha256sum "$1" | awk '{print $1}'
}

file_bytes() {
    python3 - "$1" <<'PY'
import os, sys
print(os.path.getsize(sys.argv[1]))
PY
}

download_to() {
    local url="$1"
    local dest="$2"

    if [[ "$DOWNLOADER" == "curl" ]]; then
        curl --location --fail --show-error \
            --retry 3 --retry-delay 2 --connect-timeout 30 \
            --output "$dest" "$url"
    else
        wget --tries=3 --timeout=30 --output-document="$dest" "$url"
    fi
}

record_receipt() {
    local type="$1" name="$2" url="$3" revision="$4" sha="$5" bytes="$6" action="$7"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$type" "$name" "$url" "$revision" "$sha" "$bytes" "$(utc_now)" "$action" \
        >> "$RECEIPT_TMP"
}

acquire_file() {
    local name="$1" url="$2"
    local dest="$CORPUS_DIR/$name"
    mkdir -p "$(dirname "$dest")"

    local old_url old_sha
    old_url="$(previous_field "$name" 3)"
    old_sha="$(previous_field "$name" 5)"

    if [[ "$OFFLINE" == "1" ]]; then
        [[ -f "$dest" ]] || {
            echo "ERROR: OFFLINE=1 but source file is missing: $dest" >&2
            return 1
        }

        local sha bytes
        sha="$(hash_file "$dest")"
        bytes="$(file_bytes "$dest")"

        if [[ -n "$old_sha" && "$sha" != "$old_sha" ]]; then
            echo "ERROR: offline file differs from prior receipt: $dest" >&2
            echo "       expected $old_sha" >&2
            echo "       actual   $sha" >&2
            return 1
        fi

        echo "VERIFY file (offline): $dest"
        record_receipt file "$name" "$url" - "$sha" "$bytes" OFFLINE_VERIFIED
        return
    fi

    local tmp
    tmp="$(mktemp "${dest}.download.XXXXXX")"
    echo "CHECK file: $url"
    download_to "$url" "$tmp"

    local remote_sha remote_bytes local_sha action
    remote_sha="$(hash_file "$tmp")"
    remote_bytes="$(file_bytes "$tmp")"
    local_sha=""
    [[ -f "$dest" ]] && local_sha="$(hash_file "$dest")"

    if [[ -n "$old_url" && "$old_url" != "$url" ]]; then
        action="SOURCE_URL_CHANGED"
        mv -f "$tmp" "$dest"
    elif [[ ! -f "$dest" ]]; then
        action="DOWNLOADED"
        mv -f "$tmp" "$dest"
    elif [[ "$local_sha" != "$remote_sha" ]]; then
        if [[ -n "$old_sha" && "$remote_sha" == "$old_sha" ]]; then
            action="REPAIRED_LOCAL"
        elif [[ -n "$old_sha" && "$remote_sha" != "$old_sha" ]]; then
            action="UPSTREAM_CHANGED"
        else
            action="UPDATED"
        fi
        mv -f "$tmp" "$dest"
    else
        rm -f "$tmp"
        if [[ -n "$old_sha" && "$remote_sha" != "$old_sha" ]]; then
            action="RECEIPT_REFRESHED"
        else
            action="VERIFIED"
        fi
    fi

    echo "  $action sha256=$remote_sha bytes=$remote_bytes"
    record_receipt file "$name" "$url" - "$remote_sha" "$remote_bytes" "$action"
}

git_tree_fingerprint() {
    local repo="$1"
    git -C "$repo" archive --format=tar HEAD | python3 -c '
import hashlib, sys
h = hashlib.sha256(); n = 0
while True:
    b = sys.stdin.buffer.read(1024 * 1024)
    if not b: break
    h.update(b); n += len(b)
print(h.hexdigest(), n)
'
}

reclone_repo() {
    local url="$1" dest="$2"
    rm -rf "$dest"
    git clone --depth 1 "$url" "$dest"
}

acquire_git() {
    local name="$1" url="$2"
    local dest="$CORPUS_DIR/$name"
    local old_url old_rev old_sha
    old_url="$(previous_field "$name" 3)"
    old_rev="$(previous_field "$name" 4)"
    old_sha="$(previous_field "$name" 5)"
    local action="VERIFIED"

    if [[ "$OFFLINE" == "1" ]]; then
        [[ -d "$dest/.git" ]] || {
            echo "ERROR: OFFLINE=1 but Git source is missing: $dest" >&2
            return 1
        }
        git -C "$dest" fsck --no-dangling >/dev/null
        action="OFFLINE_VERIFIED"
    else
        if [[ ! -e "$dest" ]]; then
            echo "CLONE git repo: $dest"
            git clone --depth 1 "$url" "$dest"
            action="CLONED"
        elif [[ ! -d "$dest/.git" ]]; then
            echo "ERROR: destination exists but is not a Git checkout: $dest" >&2
            return 1
        else
            local remote_url
            remote_url="$(git -C "$dest" remote get-url origin 2>/dev/null || true)"

            if [[ "$remote_url" != "$url" ]]; then
                echo "RECLONE git repo (origin changed): $dest"
                reclone_repo "$url" "$dest"
                action="SOURCE_URL_CHANGED"
            elif ! git -C "$dest" fsck --no-dangling >/dev/null 2>&1; then
                echo "RECLONE git repo (integrity failure): $dest"
                reclone_repo "$url" "$dest"
                action="REPAIRED_LOCAL"
            else
                # corpus/ is a reproducible cache, not a working tree.
                git -C "$dest" reset --hard HEAD >/dev/null
                git -C "$dest" clean -fdx >/dev/null
                echo "UPDATE git repo: $dest"
                if ! git -C "$dest" pull --ff-only --depth 1; then
                    echo "WARN: fast-forward failed; recloning $dest" >&2
                    reclone_repo "$url" "$dest"
                    action="RECLONED"
                fi
            fi
        fi
    fi

    local rev sha bytes
    rev="$(git -C "$dest" rev-parse HEAD)"
    read -r sha bytes < <(git_tree_fingerprint "$dest")

    if [[ "$action" == "VERIFIED" && -n "$old_rev" && "$rev" != "$old_rev" ]]; then
        action="UPSTREAM_CHANGED"
    elif [[ "$action" == "VERIFIED" && -n "$old_sha" && "$sha" != "$old_sha" ]]; then
        action="CONTENT_CHANGED"
    elif [[ "$action" == "VERIFIED" && -n "$old_url" && "$url" != "$old_url" ]]; then
        action="SOURCE_URL_CHANGED"
    fi

    echo "  $action commit=$rev sha256=$sha bytes=$bytes"
    record_receipt git "$name" "$url" "$rev" "$sha" "$bytes" "$action"
}

echo "============================================================"
echo "ACQUIRING / VERIFYING RAW CORPUS"
echo "Manifest: $MANIFEST"
echo "Corpus:   $CORPUS_DIR"
echo "Offline:  $OFFLINE"
echo "============================================================"

while IFS=$'\t' read -r type name url extra; do
    [[ -z "${type:-}" ]] && continue
    [[ "$type" == \#* ]] && continue

    if [[ -n "${extra:-}" || -z "${name:-}" || -z "${url:-}" ]]; then
        echo "ERROR: malformed manifest row: type<TAB>name<TAB>url required" >&2
        exit 1
    fi

    case "$type" in
        git)  acquire_git "$name" "$url" ;;
        file) acquire_file "$name" "$url" ;;
        *)
            echo "ERROR: unsupported manifest type '$type' for '$name'" >&2
            exit 1
            ;;
    esac
done < "$MANIFEST"

mv -f "$RECEIPT_TMP" "$SOURCE_RECEIPTS"
RECEIPT_TMP="$(mktemp)"

echo
echo "============================================================"
echo "BUILDING CLEAN VLL HARVEST"
echo "Harvest:  $HARVEST_DIR"
echo "Report:   $HARVEST_REPORT"
echo "Receipts: $SOURCE_RECEIPTS"
echo "============================================================"

rm -rf "$HARVEST_DIR"
mkdir -p "$HARVEST_DIR"

python3 - "$CORPUS_DIR" "$HARVEST_DIR" "$HARVEST_REPORT" <<'PY'
from __future__ import annotations

import csv
import hashlib
import html
import os
import re
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

CORPUS = Path(sys.argv[1]).resolve()
HARVEST = Path(sys.argv[2]).resolve()
REPORT = Path(sys.argv[3]).resolve()

TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".mdx", ".rst",
    ".asc", ".adoc", ".asciidoc", ".tex", ".latex",
    ".xml", ".cnxml", ".xhtml", ".html", ".htm",
    ".gram", ".grammar", ".ebnf", ".bnf", ".peg",
}

GRAMMAR_EXTS = {".gram", ".grammar", ".ebnf", ".bnf", ".peg"}
MARKUP_EXTS = {".xml", ".cnxml", ".xhtml", ".html", ".htm"}

GLOBAL_EXCLUDED_DIRS = {
    ".git", ".github", ".gitlab", ".idea", ".vscode",
    "node_modules", "vendor", "target", "dist", "build",
    "__pycache__", ".venv", "venv", ".tox", ".pytest_cache",
    "coverage", "assets", "images", "img", "fonts", "css",
    "javascript", "js", "_layouts", "_includes", "_sass",
    "META-INF", "theme", "tools",
}

MAINTENANCE_PREFIXES = (
    "readme", "license", "licence", "copying", "contributing",
    "code_of_conduct", "security", "changelog", "changes",
    "authors", "contributors", "citation", "translating",
    "translation_notes", "localization",
)

SITE_EXACT = {
    "404.md", "404.html", "404.htm", "robots.txt", "humans.txt",
    "sitemap.xml", "mimetype", "index.html",
}

OPENSTAX_SOURCES = {
    "openstax_biology",
    "openstax_chemistry",
    "openstax_university_physics",
}


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def safe_resolve_under(root: Path, base: Path, href: str) -> Path:
    candidate = (base / href).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise RuntimeError(
            f"OpenStax manifest path escapes source root: {href}"
        ) from exc
    return candidate


def load_openstax_routes() -> dict[str, set[str]]:
    """
    Build the authoritative module allowlist for each OpenStax bundle:

        META-INF/books.xml
            -> collections/*.collection.xml
            -> <col:module document="m#####">
            -> modules/m#####/index.cnxml

    The manifest/collection files are routing metadata only. They are not
    emitted into corpus_harvest/.
    """
    routes: dict[str, set[str]] = {}

    for source in sorted(OPENSTAX_SOURCES):
        root = CORPUS / source
        if not root.exists():
            continue

        books_path = root / "META-INF" / "books.xml"
        if not books_path.is_file():
            raise RuntimeError(
                f"{source}: missing OpenStax routing manifest: "
                f"{books_path.relative_to(CORPUS)}"
            )

        try:
            books_root = ET.parse(books_path).getroot()
        except ET.ParseError as exc:
            raise RuntimeError(
                f"{source}: malformed META-INF/books.xml: {exc}"
            ) from exc

        collection_paths: list[Path] = []
        for elem in books_root.iter():
            if local_name(elem.tag) != "book":
                continue
            href = (elem.attrib.get("href") or "").strip()
            if not href:
                continue
            collection_paths.append(
                safe_resolve_under(root, books_path.parent, href)
            )

        if not collection_paths:
            raise RuntimeError(
                f"{source}: META-INF/books.xml contains no collection hrefs"
            )

        module_ids: set[str] = set()

        for collection_path in collection_paths:
            if not collection_path.is_file():
                raise RuntimeError(
                    f"{source}: referenced collection is missing: "
                    f"{collection_path.relative_to(root)}"
                )

            try:
                collection_root = ET.parse(collection_path).getroot()
            except ET.ParseError as exc:
                raise RuntimeError(
                    f"{source}: malformed collection "
                    f"{collection_path.relative_to(root)}: {exc}"
                ) from exc

            for elem in collection_root.iter():
                if local_name(elem.tag) != "module":
                    continue
                document = (elem.attrib.get("document") or "").strip()
                if document:
                    module_ids.add(document)

        if not module_ids:
            raise RuntimeError(
                f"{source}: declared collections reference zero modules"
            )

        missing: list[str] = []
        for module_id in sorted(module_ids):
            module_path = root / "modules" / module_id / "index.cnxml"
            if not module_path.is_file():
                missing.append(module_id)

        if missing:
            preview = ", ".join(missing[:12])
            if len(missing) > 12:
                preview += f", ... (+{len(missing) - 12} more)"
            raise RuntimeError(
                f"{source}: {len(missing)} module(s) referenced by the "
                f"collection manifests are missing from the cloned repo: "
                f"{preview}"
            )

        routes[source] = module_ids
        print(
            f"OpenStax route: {source}: "
            f"{len(collection_paths)} collection(s), "
            f"{len(module_ids)} referenced module(s)"
        )

    return routes


OPENSTAX_ROUTES = load_openstax_routes()


# Source-specific custody rules based on the actual trees currently used by
# vll_organism/sources. Known repos use allowlists so repository machinery
# cannot enter merely because it has a text extension.
def source_profile(rel: Path) -> tuple[bool, str]:
    parts = rel.parts
    if not parts:
        return False, "empty path"

    source = parts[0]
    sub = Path(*parts[1:]) if len(parts) > 1 else Path(rel.name)
    subparts = sub.parts
    name = rel.name.lower()

    if source == "open_logic_project":
        if len(subparts) >= 1 and subparts[0] == "content" and rel.suffix.lower() in {".tex", ".txt"}:
            return True, "Open Logic content tree"
        return False, "Open Logic non-content repository machinery"

    if source == "pro_git":
        # Keep canonical section bodies only. Root/chapter wrapper files duplicate
        # or assemble these sections and would overweight Git vocabulary.
        if len(subparts) >= 4 and subparts[0] == "book" and "sections" in subparts and rel.suffix.lower() == ".asc":
            return True, "Pro Git canonical book section"
        return False, "Pro Git wrapper/metadata/non-section file"

    if source == "rust_reference":
        if len(subparts) >= 2 and subparts[0] == "src" and rel.suffix.lower() == ".md":
            if name in {"summary.md", "test-summary.md", "syntax-index.md"}:
                return False, "Rust generated/navigation index"
            return True, "Rust Reference canonical src content"
        return False, "Rust Reference dev/tooling/repository metadata"

    if source == "open_music_theory":
        # Main theory pages are root Markdown. Keep the one domain handout tree.
        if len(subparts) == 1 and rel.suffix.lower() == ".md":
            if name in {
                "404.md", "about.md", "index.md", "contents.md",
                "contents-hidden.md", "readme.md", "creategraphic.md",
                "gdrive.md", "linktotwitter.md", "typesettingkbstyle.md",
                "trinket.md", "tbdemo.md", "vat.md",
            }:
                return False, "Open Music Theory site/admin/navigation page"
            return True, "Open Music Theory theory page"
        if len(subparts) >= 3 and subparts[0] == "Graphics" and subparts[1] == "Handouts" and rel.suffix.lower() == ".md":
            return True, "Open Music Theory domain handout"
        return False, "Open Music Theory site/graphics/metadata file"

    if source in OPENSTAX_SOURCES:
        # OpenStax collection/package XML is routing metadata, not corpus
        # content. Only modules explicitly referenced by the declared book
        # collections are admitted.
        if (
            len(subparts) == 3
            and subparts[0] == "modules"
            and subparts[2] == "index.cnxml"
        ):
            module_id = subparts[1]
            if module_id in OPENSTAX_ROUTES.get(source, set()):
                return True, "OpenStax manifest-referenced textbook module"
            return False, "OpenStax unreferenced/orphan module"

        return False, "OpenStax routing/package/media metadata"

    # Standalone manifest files live directly under corpus/ and are intentionally
    # selected sources. Known unprofiled Git sources fall through to conservative
    # generic filtering below.
    return True, "generic source candidate"


def global_path_reject(rel: Path) -> str | None:
    for part in rel.parts[:-1]:
        if part in GLOBAL_EXCLUDED_DIRS:
            return f"excluded directory: {part}"

    lower = rel.name.lower()
    if lower in SITE_EXACT:
        return f"excluded site/package file: {rel.name}"
    if any(lower.startswith(prefix) for prefix in MAINTENANCE_PREFIXES):
        return f"excluded maintenance/license file: {rel.name}"
    return None


class VisibleHTML(HTMLParser):
    hidden = {"script", "style", "svg", "nav", "footer", "header", "noscript"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.hidden:
            self.depth += 1

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self.hidden and self.depth:
            self.depth -= 1
        elif not self.depth and tag in {"p", "div", "section", "article", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "th"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.depth:
            self.parts.append(data)


def decode_utf8(path: Path) -> tuple[str | None, str | None]:
    raw = path.read_bytes()
    if b"\x00" in raw:
        return None, "binary/NUL-containing file"
    try:
        return raw.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return None, "not valid UTF-8"


def strip_frontmatter(text: str) -> str:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return text
    for i in range(1, min(120, len(lines))):
        if lines[i].strip() in {"---", "..."}:
            return "".join(lines[i + 1:])
    return text


def clean_markdown(text: str) -> str:
    text = strip_frontmatter(text)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"{%.*?%}", " ", text, flags=re.S)
    text = re.sub(r"{{.*?}}", " ", text, flags=re.S)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text


def clean_asciidoc(text: str) -> str:
    out = []
    for line in text.splitlines():
        s = line.strip()
        if re.match(r"^(include::|ifdef::|ifndef::|endif::)", s):
            continue
        if re.match(r"^:[A-Za-z0-9_.-]+:\s*", s):
            continue
        out.append(line)
    return "\n".join(out)


def clean_tex(text: str) -> str:
    # Remove comments without removing math or prose.
    lines = []
    for line in text.splitlines():
        kept = []
        escaped = False
        for ch in line:
            if ch == "%" and not escaped:
                break
            kept.append(ch)
            if ch == "\\":
                escaped = not escaped
            else:
                escaped = False
        lines.append("".join(kept))
    text = "\n".join(lines)

    # Remove wrapper/import machinery that would otherwise look like content.
    text = re.sub(r"\\(?:input|include|includegraphics|olimport|documentclass|usepackage)\*?(?:\[[^\]]*\])?\{[^}]*\}", " ", text)
    text = re.sub(r"\\(?:begin|end)\{(?:document|questions|exercises|solutions)\}", " ", text)
    text = re.sub(r"\\label\{[^}]*\}", " ", text)
    return text


def clean_html(text: str) -> str:
    parser = VisibleHTML()
    parser.feed(text)
    parser.close()
    return "\n".join(parser.parts)


def clean_xml(text: str) -> str:
    try:
        root = ET.fromstring(text)
        return "\n".join(x.strip() for x in root.itertext() if x.strip())
    except ET.ParseError:
        text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
        return html.unescape(re.sub(r"<[^>]+>", " ", text))


def collapse(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    text = text.strip()
    return text + "\n" if text else ""


def clean(path: Path, text: str) -> str:
    ext = path.suffix.lower()
    if ext in {".md", ".markdown", ".mdx"}:
        text = clean_markdown(text)
    elif ext in {".asc", ".adoc", ".asciidoc"}:
        text = clean_asciidoc(text)
    elif ext in {".tex", ".latex"}:
        text = clean_tex(text)
    elif ext in {".html", ".htm", ".xhtml"}:
        text = clean_html(text)
    elif ext in {".xml", ".cnxml"}:
        text = clean_xml(text)
    return collapse(text)


def nav_heavy_markdown(original: str) -> bool:
    lines = [x.strip() for x in original.splitlines() if x.strip()]
    if len(lines) < 4:
        return False
    links = 0
    prose = 0
    for line in lines:
        if re.match(r"^[-*+]\s+\[[^\]]+\]", line):
            links += 1
        elif re.search(r"\[[^\]]+\]\([^)]+\)", line) and len(line.split()) <= 12:
            links += 1
        elif len(re.findall(r"[A-Za-z]{2,}", line)) >= 8:
            prose += 1
    return links >= 4 and links > prose * 2 and links / len(lines) >= 0.55


def reject_content(path: Path, original: str, cleaned: str) -> str | None:
    ext = path.suffix.lower()
    if not cleaned.strip():
        return "empty after cleanup"

    if ext in GRAMMAR_EXTS:
        return None if len(cleaned) >= 120 else "grammar artifact too small"

    if ext in {".md", ".markdown", ".mdx"} and nav_heavy_markdown(original):
        return "navigation/link-heavy markdown"

    alpha = sum(c.isalpha() for c in cleaned)
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", cleaned)

    if len(cleaned) < 300:
        return "too small after cleanup (<300 chars)"
    if len(words) < 45:
        return "too little prose/formal text (<45 words)"
    if alpha < 180:
        return "too little alphabetic content (<180 chars)"

    # Wrapper/manifest heuristic: many file references/macros and almost no
    # sentence-bearing prose should not become semantic chunks.
    lines = [x.strip() for x in cleaned.splitlines() if x.strip()]
    if lines:
        controlish = sum(
            1 for x in lines
            if re.match(r"^(\\[A-Za-z@]+|include::|[A-Za-z0-9_.-]+\s*=\s*[^ ]+$)", x)
        )
        if len(lines) >= 4 and controlish / len(lines) > 0.65:
            return "wrapper/configuration-dominant text"

    return None


REPORT.parent.mkdir(parents=True, exist_ok=True)
seen: dict[str, str] = {}
kept = rejected = 0

with REPORT.open("w", encoding="utf-8", newline="") as fh:
    writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
    writer.writerow(["status", "source_path", "harvest_path", "reason", "bytes_in", "bytes_out", "words"])

    for path in sorted(CORPUS.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(CORPUS)
        bytes_in = path.stat().st_size

        reason = global_path_reject(rel)
        if reason:
            rejected += 1
            writer.writerow(["REJECT", rel.as_posix(), "", reason, bytes_in, 0, 0])
            continue

        allowed, profile_reason = source_profile(rel)
        if not allowed:
            rejected += 1
            writer.writerow(["REJECT", rel.as_posix(), "", profile_reason, bytes_in, 0, 0])
            continue

        ext = path.suffix.lower()
        if ext not in TEXT_EXTS:
            rejected += 1
            writer.writerow(["REJECT", rel.as_posix(), "", f"unsupported corpus extension: {ext or '[none]'}", bytes_in, 0, 0])
            continue

        original, err = decode_utf8(path)
        if err:
            rejected += 1
            writer.writerow(["REJECT", rel.as_posix(), "", err, bytes_in, 0, 0])
            continue

        assert original is not None
        cleaned = clean(path, original)
        reason = reject_content(path, original, cleaned)
        if reason:
            rejected += 1
            writer.writerow(["REJECT", rel.as_posix(), "", reason, bytes_in, len(cleaned.encode("utf-8")), len(cleaned.split())])
            continue

        digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()
        if digest in seen:
            rejected += 1
            writer.writerow(["REJECT", rel.as_posix(), "", f"duplicate cleaned content of {seen[digest]}", bytes_in, len(cleaned.encode("utf-8")), len(cleaned.split())])
            continue
        seen[digest] = rel.as_posix()

        out_rel = rel
        if ext in MARKUP_EXTS:
            out_rel = Path(str(rel) + ".txt")

        dest = HARVEST / out_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(cleaned, encoding="utf-8")
        kept += 1
        writer.writerow(["KEEP", rel.as_posix(), out_rel.as_posix(), profile_reason, bytes_in, dest.stat().st_size, len(cleaned.split())])

print(f"Harvest kept:     {kept}")
print(f"Harvest rejected: {rejected}")
print(f"Harvest report:   {REPORT}")
PY

echo
echo "============================================================"
echo "COMPLETE"
echo "Raw corpus:       $CORPUS_DIR/"
echo "Clean harvest:    $HARVEST_DIR/"
echo "Source receipts:  $SOURCE_RECEIPTS"
echo "Harvest report:   $HARVEST_REPORT"
echo "============================================================"
