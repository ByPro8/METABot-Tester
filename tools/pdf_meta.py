import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

from pypdf import PdfReader


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} kB"
    return f"{n / (1024 * 1024):.2f} MB"


def _safe_stat_time(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).astimezone().strftime("%Y:%m:%d %H:%M:%S%z")
    except Exception:
        return ""


def _pdf_header_version(pdf_bytes: bytes) -> str:
    m = re.match(rb"%PDF-(\d\.\d)", pdf_bytes[:16])
    return m.group(1).decode("ascii", errors="ignore") if m else ""


def _detect_linearized(pdf_bytes: bytes) -> bool:
    return b"/Linearized" in pdf_bytes[:4096]


def _detect_signatures(pdf_bytes: bytes) -> bool:
    return (b"/ByteRange" in pdf_bytes) and (b"/Sig" in pdf_bytes or b"/Adobe.PPKLite" in pdf_bytes)


def _count_startxref(pdf_bytes: bytes) -> int:
    return pdf_bytes.count(b"startxref")


def _startxref_value(pdf_bytes: bytes) -> str:
    m = re.findall(rb"startxref\s+(\d+)\s+%%EOF", pdf_bytes, flags=re.S)
    if not m:
        return ""
    try:
        return m[-1].decode("ascii", errors="ignore")
    except Exception:
        return ""


def _count_eof(pdf_bytes: bytes) -> int:
    return pdf_bytes.count(b"%%EOF")


def _estimate_obj_count(pdf_bytes: bytes) -> int:
    return len(re.findall(rb"\n\d+\s+\d+\s+obj\b", pdf_bytes))


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def _python_meta_struct(pdf_path: Path, display_name: str) -> Dict[str, Dict[str, str]]:
    pdf_bytes = pdf_path.read_bytes()
    st = pdf_path.stat()

    reader = PdfReader(str(pdf_path))
    pages = len(reader.pages)
    encrypted = bool(getattr(reader, "is_encrypted", False))

    info = {}
    try:
        info = dict(reader.metadata or {})
    except Exception:
        info = {}

    trailer_keys = []
    try:
        trailer_keys = list(reader.trailer.keys())
    except Exception:
        trailer_keys = []

    doc_id = None
    try:
        doc_id = reader.trailer.get("/ID")
    except Exception:
        doc_id = None

    page0_content_sha256 = ""
    page0_fonts = []
    page0_xobjects = []
    page0_images_count = 0

    try:
        p0 = reader.pages[0]
        c = p0.get_contents()
        if c is not None:
            data = c.get_data()
            page0_content_sha256 = hashlib.sha256(data).hexdigest()

        res = p0.get("/Resources") or {}
        fonts = res.get("/Font") or {}
        page0_fonts = list(getattr(fonts, "keys", lambda: [])())
        xobj = res.get("/XObject") or {}
        page0_xobjects = list(getattr(xobj, "keys", lambda: [])())

        try:
            for k in page0_xobjects:
                xo = xobj[k]
                try:
                    if str(xo.get("/Subtype")) == "/Image":
                        page0_images_count += 1
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        pass

    # structured blocks
    blocks: Dict[str, Dict[str, str]] = {}

    blocks["System"] = {
        "File Name": display_name,
        "Directory": str(pdf_path.parent),
        "File Size": f"{_fmt_bytes(len(pdf_bytes))} ({len(pdf_bytes)} bytes)",
        "File Modify Date": _safe_stat_time(st.st_mtime),
        "File Access Date": _safe_stat_time(st.st_atime),
        "File Inode Change Date": _safe_stat_time(st.st_ctime),
    }

    blocks["Hashes"] = {
        "SHA256": _sha256(pdf_bytes),
        "MD5": _md5(pdf_bytes),
        "First 1KB SHA256": _sha256(pdf_bytes[:1024]),
        "Last  1KB SHA256": _sha256(pdf_bytes[-1024:]) if len(pdf_bytes) >= 1024 else _sha256(pdf_bytes),
    }

    blocks["PDF"] = {
        "PDF Header Version": _pdf_header_version(pdf_bytes),
        "Pages": str(pages),
        "Encrypted": str(encrypted),
        "Linearized (heuristic)": str(_detect_linearized(pdf_bytes)),
        "Signatures Present (heuristic)": str(_detect_signatures(pdf_bytes)),
        "%%EOF count": str(_count_eof(pdf_bytes)),
        "startxref count": str(_count_startxref(pdf_bytes)),
        "startxref value (last)": _startxref_value(pdf_bytes) or "(none)",
        "Object count (estimated)": str(_estimate_obj_count(pdf_bytes)),
    }

    info_block: Dict[str, str] = {}
    if info:
        for k in sorted(info.keys()):
            info_block[str(k).lstrip("/")] = str(info.get(k))
    blocks["PDF Info (Document Metadata)"] = info_block or {"(none)": ""}

    trailer_block: Dict[str, str] = {
        "Trailer Keys": ", ".join(map(str, trailer_keys)) if trailer_keys else "(none)"
    }
    if doc_id is not None:
        try:
            parts = []
            for item in doc_id:
                if isinstance(item, (bytes, bytearray)):
                    parts.append(item.hex())
                else:
                    parts.append(str(item))
            trailer_block["Document ID"] = str(parts)
        except Exception:
            trailer_block["Document ID"] = str(doc_id)
    blocks["Trailer"] = trailer_block

    blocks["Page0 Fingerprints"] = {
        "Page0 Content SHA256": page0_content_sha256 or "(none)",
        "Page0 Fonts": ", ".join(map(str, page0_fonts)) if page0_fonts else "(none)",
        "Page0 XObjects": ", ".join(map(str, page0_xobjects)) if page0_xobjects else "(none)",
        "Page0 Images Count": str(page0_images_count),
    }

    return blocks


def _format_python_meta_from_struct(struct: Dict[str, Dict[str, str]]) -> str:
    lines = []
    lines.append("---- PythonMeta ----")
    for section, kv in struct.items():
        lines.append(f"---- {section} ----")
        if not kv:
            lines.append("(none)")
            lines.append("")
            continue
        # keep stable ordering
        for k in sorted(kv.keys()):
            v = kv[k]
            if k == "(none)":
                lines.append("(none)")
            else:
                lines.append(f"{k:28}: {v}")
        lines.append("")
    return "\n".join(lines).strip()


def _format_exiftool_grouped(raw: str, display_name: str, exif_ver: str) -> str:
    groups: Dict[str, list[str]] = {}
    order: list[str] = []

    for line in raw.splitlines():
        line = line.rstrip("\n")
        m = re.match(r"^\[(.+?)\]\s*(.+?)\s*:\s*(.*)$", line)
        if not m:
            continue

        g = m.group(1).strip()
        tag = m.group(2).strip()
        val = m.group(3)

        if g.lower() == "system" and tag.lower() in ("filename", "file name"):
            val = display_name

        if g not in groups:
            groups[g] = []
            order.append(g)

        groups[g].append(f"{tag:28}: {val}")

    out = []
    out.append("---- ExifTool ----")
    out.append(f"ExifTool Version              : {exif_ver or '(unknown)'}")
    out.append("")

    for g in order:
        out.append(f"---- {g} ----")
        out.extend(groups[g])
        out.append("")

    return "\n".join(out).strip()


def _run_exiftool_raw(pdf_path: Path, display_name: str) -> Tuple[str, str]:
    runner = Path(__file__).resolve().parents[1] / "bin" / "exiftool" / "run_exiftool.sh"
    if not runner.exists():
        return "", "ExifTool not available (no bundled runner found)."

    try:
        pv = subprocess.run([str(runner), "-ver"], capture_output=True, text=True, timeout=6)
        ver = (pv.stdout or "").strip()

        proc = subprocess.run(
            [str(runner), "-a", "-G0:1", "-s", "-sort", str(pdf_path)],
            capture_output=True,
            text=True,
            timeout=25,
        )

        raw = proc.stdout or ""
        if not raw.strip():
            err = (proc.stderr or "").strip()
            msg = f"ExifTool returned no output. {('ERR: ' + err) if err else ''}".strip()
            return ver, msg

        # normalize filename for display in text log
        text_log = _format_exiftool_grouped(raw, display_name=display_name, exif_ver=ver)
        return ver, text_log

    except subprocess.TimeoutExpired:
        return "", "ExifTool timed out."
    except Exception as e:
        return "", f"ExifTool failed: {type(e).__name__}: {e}"


def _run_exiftool_struct(pdf_path: Path, display_name: str) -> Dict[str, Dict[str, str]]:
    """
    Parse ExifTool output into {Group: {Tag: Value}} using text mode with -G0:1.
    """
    runner = Path(__file__).resolve().parents[1] / "bin" / "exiftool" / "run_exiftool.sh"
    if not runner.exists():
        return {"(error)": {"message": "ExifTool not available (no bundled runner found)."}}

    try:
        proc = subprocess.run(
            [str(runner), "-a", "-G0:1", "-s", "-sort", str(pdf_path)],
            capture_output=True,
            text=True,
            timeout=25,
        )
        raw = proc.stdout or ""
        if not raw.strip():
            err = (proc.stderr or "").strip()
            return {"(error)": {"message": f"ExifTool returned no output. {('ERR: ' + err) if err else ''}".strip()}}

        groups: Dict[str, Dict[str, str]] = {}
        for line in raw.splitlines():
            m = re.match(r"^\[(.+?)\]\s*(.+?)\s*:\s*(.*)$", line)
            if not m:
                continue
            g = m.group(1).strip()
            tag = m.group(2).strip()
            val = m.group(3)

            # normalize filename
            if g.lower() == "system" and tag.lower() in ("filename", "file name"):
                val = display_name

            groups.setdefault(g, {})[tag] = val

        return groups or {"(none)": {}}

    except subprocess.TimeoutExpired:
        return {"(error)": {"message": "ExifTool timed out."}}
    except Exception as e:
        return {"(error)": {"message": f"ExifTool failed: {type(e).__name__}: {e}"}}


# ----------------------------
# PUBLIC API (unchanged)
# ----------------------------
def extract_metadata_logs(pdf_path: Path, display_name: Optional[str] = None) -> Dict[str, str]:
    name = display_name or pdf_path.name

    try:
        py_struct = _python_meta_struct(pdf_path, name)
        py = _format_python_meta_from_struct(py_struct)
    except Exception as e:
        py = f"PythonMeta failed: {type(e).__name__}: {e}"

    _, ex_text = _run_exiftool_raw(pdf_path, name)
    return {"python": py, "exiftool": ex_text}


# ----------------------------
# NEW: structured API for compare UI
# ----------------------------
def extract_metadata_structured(pdf_path: Path, display_name: Optional[str] = None) -> Dict[str, object]:
    name = display_name or pdf_path.name
    py_struct = _python_meta_struct(pdf_path, name)
    py_text = _format_python_meta_from_struct(py_struct)

    exif_struct = _run_exiftool_struct(pdf_path, name)
    _, exif_text = _run_exiftool_raw(pdf_path, name)

    return {
        "display_name": name,
        "python_struct": py_struct,
        "python_text": py_text,
        "exif_struct": exif_struct,
        "exif_text": exif_text,
    }
