import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from tools.pdf_meta import extract_metadata_logs


def _pick_value(text: str, key: str) -> str:
    m = re.search(rf"^{re.escape(key)}\s*:\s*(.*)$", text, flags=re.M)
    return (m.group(1).strip() if m else "")


def build_family_key(py_text: str, exif_text: str) -> dict:
    # Exif (pipeline identifiers)
    producer = _pick_value(exif_text, "Producer")
    creator = _pick_value(exif_text, "Creator")
    creatortool = _pick_value(exif_text, "CreatorTool")
    pdfver_exif = _pick_value(exif_text, "PDFVersion")
    xmp_toolkit = _pick_value(exif_text, "XMPToolkit")

    # Python (structural-ish)
    pdfver_py = _pick_value(py_text, "PDF Header Version")
    pages = _pick_value(py_text, "Pages")
    encrypted = _pick_value(py_text, "Encrypted")
    linearized = _pick_value(py_text, "Linearized (heuristic)")
    obj_est = _pick_value(py_text, "Object count (estimated)")
    img_cnt = _pick_value(py_text, "Page0 Images Count")

    fonts = _pick_value(py_text, "Page0 Fonts")
    xobjs = _pick_value(py_text, "Page0 XObjects")

    fonts_cnt = 0 if "(none)" in (fonts or "") else (len([x for x in (fonts or "").split(",") if x.strip()]) if fonts else 0)
    xobjs_cnt = 0 if "(none)" in (xobjs or "") else (len([x for x in (xobjs or "").split(",") if x.strip()]) if xobjs else 0)

    return {
        "producer": producer,
        "creator": creator,
        "creatortool": creatortool,
        "pdf_version": pdfver_exif or pdfver_py,
        "xmp_toolkit": xmp_toolkit,
        "pages": pages,
        "encrypted": encrypted,
        "linearized": linearized,
        "obj_est": obj_est,
        "page0_images": img_cnt,
        "fonts_cnt": str(fonts_cnt),
        "xobjects_cnt": str(xobjs_cnt),
    }


def short_key(d: dict) -> str:
    keys = [
        "producer", "creator", "creatortool", "pdf_version", "xmp_toolkit",
        "pages", "encrypted", "linearized", "obj_est", "page0_images", "fonts_cnt", "xobjects_cnt"
    ]
    return " | ".join(f"{k}={d.get(k,'')}" for k in keys)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 tools/cluster_meta_types.py <pdf_or_folder> [more...]")
        sys.exit(2)

    paths = []
    for arg in sys.argv[1:]:
        p = Path(arg)
        if p.is_dir():
            paths.extend(sorted(p.glob("*.pdf")))
        else:
            paths.append(p)

    paths = [p for p in paths if p.exists() and p.suffix.lower() == ".pdf"]
    if not paths:
        print("No PDFs found.")
        sys.exit(1)

    groups = defaultdict(list)
    details = {}

    for p in paths:
        meta = extract_metadata_logs(p, display_name=p.name)
        fam = build_family_key(meta.get("python", ""), meta.get("exiftool", ""))
        k = short_key(fam)
        groups[k].append(p.name)
        details[p.name] = fam

    print("\n=== META TYPE COUNT ===")
    print(f"Total PDFs: {len(paths)}")
    print(f"Detected families (templates needed): {len(groups)}\n")

    for i, (k, files) in enumerate(sorted(groups.items(), key=lambda x: (-len(x[1]), x[0])), start=1):
        print(f"--- FAMILY #{i} (n={len(files)}) ---")
        print(k)
        for fn in files:
            print(f"  - {fn}")
        print()

    out = {
        "total": len(paths),
        "families": [
            {"family_key": k, "count": len(v), "files": v}
            for k, v in groups.items()
        ],
        "per_file": details,
    }
    Path("output/meta_families.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Wrote: output/meta_families.json")


if __name__ == "__main__":
    main()
