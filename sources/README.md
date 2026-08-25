# Useful Corpus Experiment Resources

Usage:

```bash
chmod +x build_corpus.sh
./build_corpus.sh
```

It will create:

```text
corpus/
corpus_harvest/
```

`corpus/` keeps the original downloads/repositories. `corpus_harvest/` is rebuilt from that source tree and keeps only text-like files such as `.txt`, `.md`, `.rst`, `.tex`, `.xml`, `.adoc`, `.gram`, `.json`, `.yaml`, etc., while excluding `.git`, build outputs, virtualenvs, `node_modules`, and similar junk.
