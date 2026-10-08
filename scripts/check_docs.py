"""Check repository Markdown links, duplicate headings, and index reachability."""

from __future__ import annotations

import argparse
import re
from collections import deque
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "docs" / "README.md"
LINK = re.compile(r"(?<!!)\[[^\]]+\]\((<[^>]+>|[^)]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
EXPLICIT_ANCHOR = re.compile(r'<a id="([^"]+)"></a>')


def anchor(title: str) -> str:
    text = re.sub(r"<[^>]*>", "", title).lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def content(path: Path) -> tuple[set[str], list[str]]:
    anchors: set[str] = set()
    links: list[str] = []
    h1_count = 0
    fenced = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced:
            continue
        heading = HEADING.match(line)
        if heading:
            if line.startswith("# "):
                h1_count += 1
            key = anchor(heading.group(1))
            if key in anchors:
                raise ValueError(f"{path.relative_to(ROOT)}:{number}: duplicate anchor #{key}")
            anchors.add(key)
            for explicit in EXPLICIT_ANCHOR.findall(heading.group(1)):
                if explicit in anchors and explicit != key:
                    raise ValueError(f"{path.relative_to(ROOT)}:{number}: duplicate anchor #{explicit}")
                anchors.add(explicit)
        links.extend(match.group(1).strip("<>") for match in LINK.finditer(line))
    if h1_count != 1:
        raise ValueError(f"{path.relative_to(ROOT)}: expected one H1, found {h1_count}")
    return anchors, links


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-localizations", action="store_true")
    args = parser.parse_args()
    documents = sorted((ROOT / "docs").rglob("*.md"))
    if not args.include_localizations:
        documents = [path for path in documents if not path.is_relative_to(ROOT / "docs/pt-BR")]
    paths = [ROOT / "README.md", *documents,
             ROOT / "benchmarks" / "baseline-v1" / "README.md"]
    translated_readme = ROOT / "README.pt-BR.md"
    if args.include_localizations and translated_readme.exists():
        paths.append(translated_readme)
    parsed = {path: content(path) for path in paths}
    edges: dict[Path, set[Path]] = {path: set() for path in paths}
    for source, (_, links) in parsed.items():
        for link in links:
            parts = urlsplit(link)
            if parts.scheme or link.startswith(("mailto:", "#")) and not link.startswith("#"):
                continue
            target = (source.parent / unquote(parts.path)).resolve() if parts.path else source
            if not target.exists():
                raise ValueError(f"{source.relative_to(ROOT)}: broken link {link}")
            if parts.fragment and target in parsed and unquote(parts.fragment).lower() not in parsed[target][0]:
                raise ValueError(f"{source.relative_to(ROOT)}: missing anchor {link}")
            if target in edges:
                edges[source].add(target)
    reached = {INDEX}
    queue = deque([INDEX])
    while queue:
        for target in edges[queue.popleft()]:
            if target not in reached:
                reached.add(target)
                queue.append(target)
    missing = [path.relative_to(ROOT) for path in paths
               if path.is_relative_to(ROOT / "docs") and path not in reached]
    if missing:
        raise ValueError(f"unreachable from docs index: {missing}")
    print(f"Checked {len(paths)} Markdown files; all local links valid and docs reachable.")


if __name__ == "__main__":
    main()
