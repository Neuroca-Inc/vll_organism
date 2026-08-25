#!/usr/bin/env bash
set -Eeuo pipefail

MANIFEST="${1:-corpus_sources.tsv}"
CORPUS_DIR="${CORPUS_DIR:-corpus}"
HARVEST_DIR="${HARVEST_DIR:-corpus_harvest}"

if [[ ! -f "$MANIFEST" ]]; then
    echo "ERROR: manifest not found: $MANIFEST" >&2
    exit 1
fi

command -v git >/dev/null 2>&1 || {
    echo "ERROR: git is required." >&2
    exit 1
}

if command -v curl >/dev/null 2>&1; then
    DOWNLOADER="curl"
elif command -v wget >/dev/null 2>&1; then
    DOWNLOADER="wget"
else
    echo "ERROR: curl or wget is required." >&2
    exit 1
fi

mkdir -p "$CORPUS_DIR" "$HARVEST_DIR"

download_file() {
    local url="$1"
    local dest="$2"

    mkdir -p "$(dirname "$dest")"

    if [[ -s "$dest" ]]; then
        echo "SKIP file exists: $dest"
        return 0
    fi

    echo "GET  $url"
    if [[ "$DOWNLOADER" == "curl" ]]; then
        curl \
            --location \
            --fail \
            --show-error \
            --retry 3 \
            --retry-delay 2 \
            --connect-timeout 30 \
            --output "$dest.part" \
            "$url"
    else
        wget \
            --tries=3 \
            --timeout=30 \
            --output-document="$dest.part" \
            "$url"
    fi

    mv "$dest.part" "$dest"
}

clone_repo() {
    local url="$1"
    local dest="$2"

    if [[ -d "$dest/.git" ]]; then
        echo "UPDATE git repo: $dest"
        git -C "$dest" pull --ff-only || {
            echo "WARN: could not fast-forward $dest; leaving existing checkout unchanged." >&2
        }
        return 0
    fi

    if [[ -e "$dest" ]]; then
        echo "ERROR: destination exists but is not a git checkout: $dest" >&2
        return 1
    fi

    echo "CLONE $url"
    git clone --depth 1 "$url" "$dest"
}

echo "============================================================"
echo "DOWNLOADING CORPUS"
echo "Manifest: $MANIFEST"
echo "Corpus:   $CORPUS_DIR"
echo "============================================================"

while IFS=$'\t' read -r type name url extra; do
    [[ -z "${type:-}" ]] && continue
    [[ "$type" == \#* ]] && continue

    if [[ -n "${extra:-}" ]]; then
        echo "ERROR: malformed manifest row (too many tab-separated fields):" >&2
        printf '  %s\t%s\t%s\t%s\n' "$type" "$name" "$url" "$extra" >&2
        exit 1
    fi

    if [[ -z "${name:-}" || -z "${url:-}" ]]; then
        echo "ERROR: malformed manifest row: type/name/url are required." >&2
        exit 1
    fi

    case "$type" in
        git)
            clone_repo "$url" "$CORPUS_DIR/$name"
            ;;
        file)
            download_file "$url" "$CORPUS_DIR/$name"
            ;;
        *)
            echo "ERROR: unsupported manifest type '$type' for '$name'." >&2
            exit 1
            ;;
    esac
done < "$MANIFEST"

echo
echo "============================================================"
echo "HARVESTING TEXT SOURCES"
echo "Harvest: $HARVEST_DIR"
echo "============================================================"

# Rebuild the harvest from corpus/ so deleted/renamed source files do not
# leave stale copies behind.
rm -rf "$HARVEST_DIR"
mkdir -p "$HARVEST_DIR"

TEXT_EXTENSIONS=(
    txt
    md
    markdown
    mdx
    rst
    asc
    adoc
    asciidoc
    tex
    latex
    xml
    html
    htm
    csv
    tsv
    json
    jsonl
    yaml
    yml
    toml
    ini
    cfg
    conf
    gram
    grammar
    ebnf
    bnf
    peg
)

find_args=()
for ext in "${TEXT_EXTENSIONS[@]}"; do
    if (( ${#find_args[@]} > 0 )); then
        find_args+=( -o )
    fi
    find_args+=( -iname "*.${ext}" )
done

count=0

while IFS= read -r -d '' src; do
    rel="${src#"$CORPUS_DIR"/}"

    # Never harvest VCS metadata, build caches, or dependency/vendor trees.
    case "/$rel/" in
        */.git/*|*/node_modules/*|*/target/*|*/dist/*|*/build/*|*/__pycache__/*|*/.venv/*|*/venv/*)
            continue
            ;;
    esac

    dest="$HARVEST_DIR/$rel"
    mkdir -p "$(dirname "$dest")"
    cp -p "$src" "$dest"
    ((count += 1))
done < <(
    find "$CORPUS_DIR" \
        -type f \
        \( "${find_args[@]}" \) \
        -print0
)

echo
echo "============================================================"
echo "COMPLETE"
echo "Downloaded corpus:  $CORPUS_DIR/"
echo "Text harvest:       $HARVEST_DIR/"
echo "Harvested files:    $count"
echo "============================================================"
