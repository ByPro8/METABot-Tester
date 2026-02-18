from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import time
import uuid

from fastapi import FastAPI, File, UploadFile, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from tools.pdf_meta import extract_metadata_logs, extract_metadata_structured
from tools.cluster_meta_types import build_family_key, short_key
from tools.tchk import run_template_check

BASE_DIR = Path(__file__).resolve().parents[1]
UPLOAD_DIR = BASE_DIR / "output" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# In-memory cache for cluster results (prevents POST refresh resubmission)
CLUSTER_CACHE: dict[str, dict] = {}
CLUSTER_TTL_SECONDS = 60 * 60  # 1 hour

app = FastAPI(title="MetaBot Lab")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def safe_name(name: str) -> str:
    name = (name or "upload.pdf").replace("/", "_").replace("\\", "_").strip()
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name


def _flatten_struct(struct: Dict[str, Dict[str, str]], prefix: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for section, kv in (struct or {}).items():
        if not isinstance(kv, dict):
            continue
        for k, v in kv.items():
            if k == "(none)":
                continue
            key = f"{prefix}.{section}.{k}"
            out[key] = "" if v is None else str(v)
    return out


# ------------------------------
# IGNORE RULES (compare/cluster)
# ------------------------------

# Exact keys to ignore (stable baseline)
_IGNORE_KEY_EXACT_BASE: set[str] = {
    # Python volatile
    "py.System.File Modify Date",
    "py.System.File Access Date",
    "py.System.File Inode Change Date",
    "py.System.Directory",
    "py.System.File Size",
    "py.System.File Name",
    "py.Hashes.SHA256",
    "py.Hashes.MD5",
    "py.Hashes.First 1KB SHA256",
    "py.Hashes.Last  1KB SHA256",
    # Exif volatile
    "exif.File:System.FileAccessDate",
    "exif.File:System.FileInodeChangeDate",
    "exif.File:System.FileModifyDate",
    "exif.File:System.Directory",
    "exif.File:System.FileName",
    "exif.File:System.FileSize",
    "exif.File:System.FilePermissions",
    # ExifTool version (noise for family building)
    "exif.ExifTool.ExifToolVersion",
}


# Pattern ignores: remove timestamps that are usually unstable across downloads
_IGNORE_KEY_PATTERNS: list[tuple[str, "re.Pattern[str]"]] = []


def _build_ignore_patterns():
    import re

    pats: list[tuple[str, "re.Pattern[str]"]] = []

    # ExifTool often emits these date-ish tags in various groups
    pats.append(
        (
            "exif dates",
            re.compile(
                r"^exif\..*\.(CreateDate|ModifyDate|CreationDate|MetadataDate)$"
            ),
        )
    )

    # pypdf Info keys (your PythonMeta "PDF Info (Document Metadata)")
    # banks differ here, but date fields are noisy and not useful for template building.
    pats.append(
        (
            "python info dates",
            re.compile(
                r"^py\..*\.(CreationDate|ModDate|CreateDate|ModifyDate|MetadataDate)$"
            ),
        )
    )

    return pats


_IGNORE_KEY_PATTERNS = _build_ignore_patterns()


def _is_ignored(key: str, ignore_exact: set[str]) -> bool:
    if key in ignore_exact:
        return True
    for _name, rx in _IGNORE_KEY_PATTERNS:
        if rx.match(key):
            return True
    return False


def _split_flat_key(key: str) -> tuple[str, str, str]:
    """Split 'py.Section.Tag' or 'exif.Group.Tag' into (source, section, tag)."""
    parts = (key or "").split(".", 2)
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return parts[0] if parts else "", "", ""


def _group_same_for_ui(items: List[Tuple[str, str]]) -> List[Dict[str, object]]:
    """Convert [(flat_key,value)] into UI-friendly grouped sections."""
    grouped: Dict[str, List[Tuple[str, str]]] = {}
    for k, v in items:
        _src, section, tag = _split_flat_key(k)
        section_disp = (section or "(none)").replace(":", " / ")
        grouped.setdefault(section_disp, []).append((tag or "(none)", v))

    out: List[Dict[str, object]] = []
    for sec in sorted(grouped.keys()):
        rows = sorted(grouped[sec], key=lambda it: it[0])
        out.append({"section": sec, "rows": rows})
    return out


def _group_diff_for_ui(
    items: List[Tuple[str, Dict[str, str]]], files: List[str]
) -> List[Dict[str, object]]:
    """Convert [(flat_key,{file:value})] into grouped sections for UI."""
    grouped: Dict[str, List[Dict[str, object]]] = {}
    for k, vals in items:
        _src, section, tag = _split_flat_key(k)
        section_disp = (section or "(none)").replace(":", " / ")
        grouped.setdefault(section_disp, []).append(
            {"tag": tag or "(none)", "vals": vals}
        )

    out: List[Dict[str, object]] = []
    for sec in sorted(grouped.keys()):
        rows = sorted(grouped[sec], key=lambda it: str(it.get("tag", "")))
        # ensure missing keys exist for template rendering consistency
        for row in rows:
            vv = row.get("vals") or {}
            for f in files:
                vv.setdefault(f, "(missing)")
            row["vals"] = vv
        out.append({"section": sec, "rows": rows})
    return out


def _group_keys_for_ui(keys: List[str]) -> List[Dict[str, object]]:
    """Group flat keys into sections for display on the Cluster page."""
    grouped: Dict[str, List[str]] = {}
    for k in keys or []:
        _src, section, tag = _split_flat_key(k)
        section_disp = (section or "(none)").replace(":", " / ")
        grouped.setdefault(section_disp, []).append(tag or "(none)")

    out: List[Dict[str, object]] = []
    for sec in sorted(grouped.keys()):
        tags = sorted(set(grouped[sec]))
        out.append({"section": sec, "tags": tags})
    return out


def _group_by_keyset(
    flat_per_file: Dict[str, Dict[str, str]], ignore_exact: set[str]
) -> List[Dict[str, object]]:
    """
    Group files by the SET of available metadata keys (presence/absence only).
    Returns list of groups with label A/B/C..., keys list, and files.
    """
    # build keyset per file
    keyset_to_files: Dict[frozenset, List[str]] = {}
    for fn, kv in flat_per_file.items():
        keys = {k for k in kv.keys() if not _is_ignored(k, ignore_exact)}
        fs = frozenset(sorted(keys))
        keyset_to_files.setdefault(fs, []).append(fn)

    # stable ordering: biggest groups first, then by key count, then name
    groups = sorted(
        keyset_to_files.items(),
        key=lambda it: (-len(it[1]), -len(it[0]), ",".join(sorted(it[1]))),
    )

    labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    out: List[Dict[str, object]] = []
    for i, (keyset, files) in enumerate(groups):
        label = labels[i] if i < len(labels) else f"G{i+1}"
        out.append(
            {
                "label": label,
                "count": len(files),
                "files": sorted(files),
                "keys": sorted(list(keyset)),
            }
        )
    return out


def _compare_many(
    flat_per_file: Dict[str, Dict[str, str]], ignore_exact: set[str]
) -> Tuple[List[Tuple[str, str]], List[Tuple[str, Dict[str, str]]]]:
    files = list(flat_per_file.keys())
    all_keys = set()
    for fn in files:
        all_keys |= set(flat_per_file[fn].keys())

    all_keys = {k for k in all_keys if not _is_ignored(k, ignore_exact)}

    same_list: List[Tuple[str, str]] = []
    diff_list: List[Tuple[str, Dict[str, str]]] = []

    for k in sorted(all_keys):
        vals: Dict[str, str] = {}
        missing = False
        for fn in files:
            if k not in flat_per_file[fn]:
                vals[fn] = "(missing)"
                missing = True
            else:
                vals[fn] = flat_per_file[fn][k]

        if not missing:
            uniq = set(vals.values())
            if len(uniq) == 1:
                same_list.append((k, next(iter(uniq))))
            else:
                diff_list.append((k, vals))
        else:
            diff_list.append((k, vals))

    return same_list, diff_list


def _cluster_cache_cleanup() -> None:
    now = time.time()
    dead = [
        k
        for k, v in CLUSTER_CACHE.items()
        if (now - float(v.get("ts", now))) > CLUSTER_TTL_SECONDS
    ]
    for k in dead:
        CLUSTER_CACHE.pop(k, None)


def _cluster_cache_set(payload: dict) -> str:
    _cluster_cache_cleanup()
    cid = str(uuid.uuid4())
    CLUSTER_CACHE[cid] = {"ts": time.time(), "payload": payload}
    return cid


def _cluster_cache_get(cid: str) -> dict | None:
    _cluster_cache_cleanup()
    item = CLUSTER_CACHE.get(cid)
    return item.get("payload") if item else None


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "result": None, "compare": None, "error": None},
    )


@app.post("/analyze", response_class=HTMLResponse)
async def analyze(request: Request, pdf: UploadFile = File(...)):
    try:
        name = safe_name(pdf.filename or "upload.pdf")
        out_path = UPLOAD_DIR / name
        out_path.write_bytes(await pdf.read())

        logs = extract_metadata_logs(out_path, display_name=name)
        fam = build_family_key(logs.get("python", ""), logs.get("exiftool", ""))
        fam_key = short_key(fam)

        result = {
            "filename": name,
            "family": fam,
            "family_key": fam_key,
            "python_log": logs.get("python", ""),
            "exif_log": logs.get("exiftool", ""),
        }

        return templates.TemplateResponse(
            "index.html",
            {"request": request, "result": result, "compare": None, "error": None},
        )

    except Exception as e:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "result": None,
                "compare": None,
                "error": f"{type(e).__name__}: {e}",
            },
        )


@app.post("/template-check", response_class=HTMLResponse)
async def template_check(
    request: Request,
    pdf: UploadFile = File(...),
    template_id: str = Form("TEB_MAIN_V1"),
):
    """
    Single-PDF check against a stored ExifTool metadata template (v1).
    Current v1 template: TEB_MAIN_V1
    Rules:
      - Ignore noisy ExifTool groups/tags (see template ignore rules)
      - STRICT keyset: any extra meaningful keys -> FAIL
      - Expected values must match exactly -> FAIL
    """
    try:
        name = safe_name(pdf.filename or "upload.pdf")
        out_path = UPLOAD_DIR / name
        out_path.write_bytes(await pdf.read())

        meta = extract_metadata_structured(out_path, display_name=name)
        report = run_template_check(
            exif_struct=meta["exif_struct"],
            filename=name,
            template_id=template_id,
            file_size_bytes=out_path.stat().st_size,
            exif_text=meta.get("exif_text", ""),
        )

        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "result": None,
                "compare": None,
                "template_check": report,
                "error": None,
            },
        )

    except Exception as e:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "result": None,
                "compare": None,
                "template_check": None,
                "error": f"{type(e).__name__}: {e}",
            },
        )


@app.post("/sizes", response_class=HTMLResponse)
async def sizes(request: Request, pdfs: List[UploadFile] = File(...)):
    """
    Upload many PDFs and print each file size (KB + bytes).
    Used to help decide a size-range rule for a template family.
    """
    try:
        items = []
        for up in pdfs:
            name = safe_name(up.filename or "upload.pdf")
            out_path = UPLOAD_DIR / name
            data = await up.read()
            out_path.write_bytes(data)
            b = out_path.stat().st_size
            kb = b / 1024.0
            items.append({"name": name, "bytes": b, "kb": kb})

        items = sorted(items, key=lambda x: x["name"].lower())

        kbs = [it["kb"] for it in items] or [0.0]
        summary = {
            "count": len(items),
            "min_kb": min(kbs),
            "max_kb": max(kbs),
            "avg_kb": sum(kbs) / len(kbs) if items else 0.0,
        }

        # Plain text log for the UI <pre>
        lines = ["==== FILE SIZES (KB) ===="]
        for it in items:
            lines.append(f"{it['name']} : {it['kb']:.2f} kB ({it['bytes']} bytes)")
        lines.append("")
        lines.append(f"Count: {summary['count']}")
        lines.append(f"Min KB: {summary['min_kb']:.2f}")
        lines.append(f"Max KB: {summary['max_kb']:.2f}")
        lines.append(f"Avg KB: {summary['avg_kb']:.2f}")
        log = "\n".join(lines).rstrip() + "\n"

        payload = {"items": items, "summary": summary, "log": log}
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "result": None,
                "compare": None,
                "template_check": None,
                "sizes": payload,
                "error": None,
            },
        )

    except Exception as e:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "result": None,
                "compare": None,
                "template_check": None,
                "sizes": None,
                "error": f"{type(e).__name__}: {e}",
            },
        )


@app.get("/cluster")
def cluster_get():
    # Entry point is home; avoid 405 and avoid browser POST re-submit behavior
    return RedirectResponse(url="/", status_code=303)


@app.post("/cluster", response_class=HTMLResponse)
async def cluster(request: Request, pdfs: List[UploadFile] = File(...)):
    """
    Upload many PDFs and group them by AVAILABLE METADATA KEYS (presence/absence only),
    separately for PythonMeta vs ExifTool.
    """
    try:
        saved = []
        for up in pdfs:
            name = safe_name(up.filename or "upload.pdf")
            out_path = UPLOAD_DIR / name
            out_path.write_bytes(await up.read())
            saved.append(out_path)

        per_file = {}
        for pth in saved:
            meta = extract_metadata_structured(pth, display_name=pth.name)
            per_file[pth.name] = meta

        python_flat = {
            fn: _flatten_struct(per_file[fn]["python_struct"], "py") for fn in per_file
        }
        exif_flat = {
            fn: _flatten_struct(per_file[fn]["exif_struct"], "exif") for fn in per_file
        }

        ignore_exact = _IGNORE_KEY_EXACT_BASE

        py_groups = _group_by_keyset(python_flat, ignore_exact=ignore_exact)
        ex_groups = _group_by_keyset(exif_flat, ignore_exact=ignore_exact)

        # Precompute UI-friendly grouped key lists (remove 'py./exif.' prefixes)
        for g in py_groups:
            g["keys_grouped"] = _group_keys_for_ui(g.get("keys", []))
        for g in ex_groups:
            g["keys_grouped"] = _group_keys_for_ui(g.get("keys", []))

        result = {
            "files": sorted(list(per_file.keys())),
            "python_groups": py_groups,
            "exif_groups": ex_groups,
        }

        cid = _cluster_cache_set({"cluster": result, "error": None})
        return RedirectResponse(url=f"/cluster/result/{cid}", status_code=303)

    except Exception as e:
        cid = _cluster_cache_set({"cluster": None, "error": f"{type(e).__name__}: {e}"})
        return RedirectResponse(url=f"/cluster/result/{cid}", status_code=303)


@app.get("/cluster/result/{cid}", response_class=HTMLResponse)
def cluster_result(request: Request, cid: str):
    data = _cluster_cache_get(cid)
    if not data:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        "cluster.html",
        {
            "request": request,
            "cluster": data.get("cluster"),
            "error": data.get("error"),
        },
    )


@app.post("/compare", response_class=HTMLResponse)
async def compare(request: Request, pdfs: List[UploadFile] = File(...)):
    try:
        saved = []
        for up in pdfs:
            name = safe_name(up.filename or "upload.pdf")
            out_path = UPLOAD_DIR / name
            out_path.write_bytes(await up.read())
            saved.append(out_path)

        per_file = {}
        families = {}
        family_keys = {}

        for p in saved:
            meta = extract_metadata_structured(p, display_name=p.name)
            per_file[p.name] = meta

            fam = build_family_key(meta["python_text"], meta["exif_text"])
            families[p.name] = fam
            family_keys[p.name] = short_key(fam)

        python_flat = {
            fn: _flatten_struct(per_file[fn]["python_struct"], "py") for fn in per_file
        }
        exif_flat = {
            fn: _flatten_struct(per_file[fn]["exif_struct"], "exif") for fn in per_file
        }

        ignore_exact = _IGNORE_KEY_EXACT_BASE

        py_same, py_diff = _compare_many(python_flat, ignore_exact=ignore_exact)
        ex_same, ex_diff = _compare_many(exif_flat, ignore_exact=ignore_exact)

        uniq_family = set(family_keys.values())
        family_all_same = len(uniq_family) == 1

        files = list(per_file.keys())

        compare_result = {
            "files": files,
            "python": {
                "same": py_same,
                "diff": py_diff,
                "same_grouped": _group_same_for_ui(py_same),
                "diff_grouped": _group_diff_for_ui(py_diff, files),
            },
            "exif": {
                "same": ex_same,
                "diff": ex_diff,
                "same_grouped": _group_same_for_ui(ex_same),
                "diff_grouped": _group_diff_for_ui(ex_diff, files),
            },
            "families": families,
            "family_keys": family_keys,
            "family_all_same": family_all_same,
        }

        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "result": None,
                "compare": compare_result,
                "error": None,
            },
        )

    except Exception as e:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "result": None,
                "compare": None,
                "error": f"{type(e).__name__}: {e}",
            },
        )


@app.get("/raw/{filename}")
def raw_pdf(filename: str):
    """
    Serve uploaded PDF bytes for in-browser viewing.
    """
    name = safe_name(filename)
    path = UPLOAD_DIR / name
    if not path.exists():
        return Response(status_code=404, content="Not found")
    # inline => browser opens PDF viewer
    return FileResponse(
        str(path),
        media_type="application/pdf",
        filename=name,
        headers={"Content-Disposition": f'inline; filename="{name}"'},
    )


@app.get("/view/{filename}", response_class=HTMLResponse)
def view_pdf(request: Request, filename: str):
    """
    HTML wrapper to force tab title = filename, then embed the PDF.
    """
    name = safe_name(filename)
    path = UPLOAD_DIR / name
    if not path.exists():
        return Response(status_code=404, content="Not found")
    return templates.TemplateResponse(
        "view.html",
        {"request": request, "filename": name},
    )


@app.get("/healthz")
def healthz():
    return {"ok": True}
