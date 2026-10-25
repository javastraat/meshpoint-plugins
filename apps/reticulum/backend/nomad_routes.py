"""NomadNet browsing + page-editing endpoints for the Reticulum page.

`/api/reticulum/nomad/nodes` lists the `nomadnetwork.node` peers already
in the roster (announces the plugin's LxmfService heard); `/page` fetches
one page's Micron markup over a Link; `/file` fetches a `/file/...` path
as an attachment (see `backend/nomad.py`).

`/api/reticulum/nomad/pages*` is the other direction -- read/write the
local `.mu` files a hosted node serves, backing the page's "Pages" tab
(see `backend/node_pages.py`).
"""

from __future__ import annotations

import mimetypes

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from src.api.auth.dependencies import require_admin, require_auth
from src.api.auth.jwt_session import SessionClaims

from . import node_pages, nomad, scraper, state
from .lxmf_service import LxmfService

router = APIRouter(prefix="/api/reticulum/nomad", tags=["reticulum"])

_service: LxmfService | None = None


def init_routes(service: LxmfService) -> None:
    global _service
    _service = service
    nomad.set_timeouts(state.to_dict().get("nomad_timeout_s"))


def reset_routes() -> None:
    global _service
    _service = None
    nomad.reset()


# The public network has thousands of nomadnetwork.node announces; the
# picker only needs the recently-active ones (anything else is still
# reachable by pasting its hash into the address bar).
_NODE_LIMIT = 300


@router.get("/nodes")
async def nomad_nodes(_claims: SessionClaims = Depends(require_auth)):
    """The most recently-seen `nomadnetwork.node` destinations (capped)."""
    if _service is None:
        raise HTTPException(503, "Reticulum companion is disabled")
    peers = await _service.list_peers()  # already sorted last_seen DESC
    nodes = [p.to_dict() for p in peers if p.aspect == "nomadnetwork.node"]
    return nodes[:_NODE_LIMIT]


class PageRequest(BaseModel):
    destination_hash: str = Field(..., min_length=1)
    path: str = "/page/index.mu"
    field_data: dict | None = None


@router.post("/page")
async def nomad_page(
    req: PageRequest, _claims: SessionClaims = Depends(require_auth),
):
    """Fetch one NomadNet page. Returns `{ok, content}` (Micron markup) or
    `{ok: false, error}` -- a fetch failure is a 200 with `ok: false`, not
    an HTTP error, so the Browse tab can show it inline."""
    result = await nomad.fetch_page(
        req.destination_hash, req.path or "/page/index.mu", req.field_data,
    )
    return {
        "ok": result.ok,
        "content": result.content,
        "error": result.error,
        "destination_hash": result.destination_hash,
        "path": result.path,
    }


class FingerprintRequest(BaseModel):
    destination_hash: str = Field(..., min_length=1)


@router.post("/fingerprint")
async def nomad_fingerprint(
    req: FingerprintRequest, _claims: SessionClaims = Depends(require_auth),
):
    """Identify our own Reticulum identity to a NomadNet node over its
    Link -- some `.mu` pages (guestbooks, registration forms) read the
    resulting LXMF address from a form submission to know who's visiting.
    Ported in spirit from rBrowser's fingerprint action (see backend/nomad.py
    for the full explanation); a fetch failure is a 200 with `ok: false`,
    matching the `/page` route's convention."""
    if _service is None:
        raise HTTPException(503, "Reticulum companion is disabled")
    ok, result = await nomad.identify_link(req.destination_hash, _service.identity)
    return {"ok": ok, "lxmf_hash": result if ok else None, "error": None if ok else result}


@router.post("/disconnect")
async def nomad_disconnect(
    req: FingerprintRequest, _claims: SessionClaims = Depends(require_auth),
):
    """Tear down the cached Link to this node (see `nomad.py`'s
    `drop_link()`) -- the only way to make a node "forget" an identify,
    since RNS has no reverse of `link.identify()`. Same auth tier as
    `/fingerprint`: a browsing action, not an edit to this node's own
    hosted content. Affects every consumer of the shared Link cache for
    that destination, not just the caller."""
    dropped = nomad.drop_link(req.destination_hash)
    return {"dropped": dropped}


@router.get("/links")
async def nomad_links(_claims: SessionClaims = Depends(require_auth)):
    """How many NomadNet Links this process currently has cached -- feeds
    the Reticulum page's "disconnect all" count. Shared across every
    consumer of the cache (Browse tab, Reticulum Browser, the Dashboard's
    quick-browse modal), same as `/disconnect`."""
    return {"count": nomad.link_count()}


@router.post("/disconnect-all")
async def nomad_disconnect_all(_claims: SessionClaims = Depends(require_admin)):
    """Tear down every cached NomadNet Link at once (see `nomad.py`'s
    `drop_all_links()`) -- the manual counterpart to the idle sweep that
    already runs on a timer (`lxmf_service.py`'s `_link_sweep_loop`).
    Admin-gated (unlike `/disconnect`, which is a single-destination
    browsing action) since it affects every open tab/consumer at once."""
    dropped = nomad.drop_all_links()
    return {"dropped": dropped}


class FileRequest(BaseModel):
    destination_hash: str = Field(..., min_length=1)
    path: str = Field(..., min_length=1)


@router.post("/file")
async def nomad_file(
    req: FileRequest, _claims: SessionClaims = Depends(require_auth),
):
    """Fetch a `/file/...` path -- returns the raw bytes as an attachment,
    or a 502 with the failure reason."""
    result = await nomad.fetch_file(req.destination_hash, req.path)
    if not result.ok:
        raise HTTPException(502, result.error or "download failed")
    name = result.file_name or "downloaded_file"
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return Response(
        content=result.file_bytes or b"",
        media_type=ctype,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# --- Pages tab: edit the .mu files this node serves ----------------------

def _pages_dir() -> str:
    return state.node_config()["pages_dir"]


def _reload_node() -> bool:
    """Ask the running node (if any) to re-register its .mu handlers."""
    return bool(_service and _service.reload_node_pages())


class PageWrite(BaseModel):
    content: str = Field(..., max_length=node_pages.MAX_PAGE_BYTES * 2)


@router.get("/pages")
async def list_node_pages(_claims: SessionClaims = Depends(require_admin)):
    """Every editable `*.mu` file in `node_pages_dir` (index.mu first).
    `node` reports whether a node is actually hosting right now -- edits
    still save when it isn't, they just won't be served until it starts."""
    return {
        "pages": node_pages.list_pages(_pages_dir()),
        "pages_dir": _pages_dir(),
        "node_hosting": bool(_service and _service.node_status()),
    }


@router.get("/sample-page")
async def node_sample_page(_claims: SessionClaims = Depends(require_admin)):
    """The bundled `sample-pages/index.mu` -- the 'Load sample' button."""
    return {"content": node_pages.sample_index()}


@router.get("/pages/{name:path}")
async def read_node_page(
    name: str, _claims: SessionClaims = Depends(require_admin),
):
    try:
        node_pages.validate_name(name)
    except node_pages.PageError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"name": name, "content": node_pages.read_page(_pages_dir(), name)}


@router.put("/pages/{name:path}")
async def write_node_page(
    name: str, req: PageWrite,
    _claims: SessionClaims = Depends(require_admin),
):
    try:
        meta = node_pages.write_page(_pages_dir(), name, req.content)
    except node_pages.PageError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"saved": True, "page": meta, "served": _reload_node()}


@router.delete("/pages/{name:path}")
async def delete_node_page(
    name: str, _claims: SessionClaims = Depends(require_admin),
):
    try:
        node_pages.delete_page(_pages_dir(), name)
    except node_pages.PageError as exc:
        raise HTTPException(400, str(exc)) from exc
    _reload_node()
    return {"deleted": True, "name": name}


@router.delete("/pages-folder/{name}")
async def delete_node_folder(
    name: str, _claims: SessionClaims = Depends(require_admin),
):
    """Delete an entire collection subfolder (e.g. a scraped site) and
    every page in it. Same underlying call as the Scrape tab's own
    DELETE /api/reticulum/scrape/collections/{name} -- kept as a second,
    Pages-tab-scoped route rather than having this editor's frontend reach
    into an unrelated feature's URL space for what is, from here, just
    "delete a folder"."""
    try:
        scraper.delete_collection(_pages_dir(), name)
    except node_pages.PageError as exc:  # scraper.ScraperError is a PageError
        raise HTTPException(400, str(exc)) from exc
    return {"deleted": True, "name": name, "served": _reload_node()}
