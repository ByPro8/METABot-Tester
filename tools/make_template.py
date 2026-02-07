import csv
import json
from datetime import datetime
from pathlib import Path

from tools.pdf_meta import extract_metadata_logs
from tools.cluster_meta_types import build_family_key, short_key


IGNORE_ALWAYS = {
    "system.FileAccessDate",
    "system.FileModifyDate",
    "system.FileInodeChangeDate",
    "system.FileName",
    "hashes.SHA256",
    "hashes.MD5",
    "hashes.First 1KB SHA256",
    "hashes.Last  1KB SHA256",
}


def main():
    import sys
    if len(sys.argv) < 3:
        print("Usage: python -m tools.make_template <bank_name> <pdf_folder>")
        raise SystemExit(2)

    bank = sys.argv[1].strip()
    folder = Path(sys.argv[2]).resolve()
    pdfs = sorted(folder.glob("*.pdf"))
    if not pdfs:
        print("No PDFs found in:", folder)
        raise SystemExit(1)

    # Collect family keys per file
    fam_map = {}
    fam_groups = {}
    for p in pdfs:
        meta = extract_metadata_logs(p, display_name=p.name)
        fam = build_family_key(meta.get("python", ""), meta.get("exiftool", ""))
        k = short_key(fam)
        fam_map[p.name] = fam
        fam_groups.setdefault(k, []).append(p)

    out_root = Path("output/templates") / bank
    out_root.mkdir(parents=True, exist_ok=True)

    for idx, (fam_key, files) in enumerate(sorted(fam_groups.items(), key=lambda x: (-len(x[1]), x[0])), start=1):
        fam_dir = out_root / f"family_{idx}"
        fam_dir.mkdir(parents=True, exist_ok=True)

        # Compute ranges for numeric-ish fields
        def _ints(field: str):
            vals = []
            for p in files:
                v = fam_map[p.name].get(field, "")
                try:
                    vals.append(int(str(v).strip()))
                except Exception:
                    pass
            return vals

        obj_vals = _ints("obj_est")
        img_vals = _ints("page0_images")
        fonts_vals = _ints("fonts_cnt")
        xobj_vals = _ints("xobjects_cnt")

        template = {
            "bank": bank,
            "family": f"family_{idx}",
            "created_at": datetime.now().astimezone().isoformat(),
            "samples_count": len(files),
            "expected": {
                "producer": sorted({fam_map[p.name].get("producer", "") for p in files}),
                "creator": sorted({fam_map[p.name].get("creator", "") for p in files}),
                "creatortool": sorted({fam_map[p.name].get("creatortool", "") for p in files}),
                "pdf_version": sorted({fam_map[p.name].get("pdf_version", "") for p in files}),
                "xmp_toolkit": sorted({fam_map[p.name].get("xmp_toolkit", "") for p in files}),
                "pages": sorted({fam_map[p.name].get("pages", "") for p in files}),
                "encrypted": sorted({fam_map[p.name].get("encrypted", "") for p in files}),
                "linearized": sorted({fam_map[p.name].get("linearized", "") for p in files}),
            },
            "ranges": {
                "obj_est": [min(obj_vals), max(obj_vals)] if obj_vals else None,
                "page0_images": [min(img_vals), max(img_vals)] if img_vals else None,
                "fonts_cnt": [min(fonts_vals), max(fonts_vals)] if fonts_vals else None,
                "xobjects_cnt": [min(xobj_vals), max(xobj_vals)] if xobj_vals else None,
            },
            "ignore": sorted(IGNORE_ALWAYS),
            "notes": "",
        }

        (fam_dir / "template.json").write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")

        # index.csv
        with (fam_dir / "index.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["filename", "collected_at", "comment"])
            now = datetime.now().strftime("%Y-%m-%d")
            for p in files:
                w.writerow([p.name, now, ""])

        # notes.md
        notes = []
        notes.append(f"# {bank} — family_{idx}")
        notes.append("")
        notes.append(f"Samples: {len(files)}")
        notes.append("")
        notes.append("## Family key")
        notes.append("```")
        notes.append(fam_key)
        notes.append("```")
        notes.append("")
        notes.append("## Template summary")
        notes.append("```json")
        notes.append(json.dumps(template, ensure_ascii=False, indent=2))
        notes.append("```")
        (fam_dir / "notes.md").write_text("\n".join(notes), encoding="utf-8")

        print("Wrote:", fam_dir / "template.json")

    print("\nDone. Templates in:", out_root)


if __name__ == "__main__":
    main()
