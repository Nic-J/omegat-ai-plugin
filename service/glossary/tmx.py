from pathlib import Path
from xml.etree import ElementTree as ET

import structlog

log = structlog.get_logger()


def parse_tmx(path: Path, src_lang: str, tgt_lang: str) -> list[tuple[str, str]]:
    """Parse a TMX file and return (source, target) segment pairs.

    Language matching is prefix-based and case-insensitive, so "en" matches
    "EN", "EN-US", "en-GB", etc.
    """
    tree = ET.parse(path)
    root = tree.getroot()

    src_prefix = src_lang.lower()
    tgt_prefix = tgt_lang.lower()

    pairs: list[tuple[str, str]] = []
    tu_count = 0
    for tu in root.iter("tu"):
        tu_count += 1
        src_seg = tgt_seg = None
        for tuv in tu.findall("tuv"):
            # TMX uses xml:lang (XML namespace) or plain lang attribute
            lang = (
                tuv.get("{http://www.w3.org/XML/1998/namespace}lang")
                or tuv.get("lang")
                or ""
            ).lower()
            seg_el = tuv.find("seg")
            if seg_el is None or not seg_el.text:
                continue
            text = seg_el.text.strip()
            if lang.startswith(src_prefix):
                src_seg = text
            elif lang.startswith(tgt_prefix):
                tgt_seg = text
        if src_seg and tgt_seg:
            pairs.append((src_seg, tgt_seg))

    if tu_count and len(pairs) < tu_count:
        log.debug("tmx_unmatched_tu", path=str(path), tu_count=tu_count, paired=len(pairs))

    return pairs
