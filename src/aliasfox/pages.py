"""Public pages for multi mode: landing (`/` when signed out), privacy, terms.

Plain HTML and one stylesheet, rendered once at startup from
`page_templates/`. Placeholders:

- `{{CONTACT}}`: one or two sentences saying where to write (CONTACT_EMAIL,
  escaped) or, when unset, to use the GitHub repository.
- `{{PUBLIC_URL}}`: PUBLIC_URL without a trailing slash, escaped. When it is
  unset, every template line containing it is dropped, so the pages carry no
  absolute URLs of their own.
- `{{SIGNUPS}}`: the sign-up call to action, or a "closed" notice.

Every configured value goes through `html.escape(quote=True)`; the only raw
markup inserted is fixed text from this module."""

import html
from importlib import resources

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from aliasfox.auth import has_valid_session
from aliasfox.config import Settings
from aliasfox.web import NO_CACHE

REPO_URL = "https://github.com/codecrumb/aliasfox"
CSS_CACHE = "public, max-age=3600"
PAGES = {"/": "landing.html", "/privacy": "privacy.html", "/terms": "terms.html"}

SIGNUPS_OPEN = '<a class="button primary" href="/signup">Sign up</a>'
SIGNUPS_CLOSED = '<p class="notice">Sign-ups are closed for now.</p>'


def _esc(value: str) -> str:
    return html.escape(value, quote=True)


def contact_html(email: str | None) -> str:
    if email and email.strip():
        e = _esc(email.strip())
        return f'Write to <a href="mailto:{e}">{e}</a>.'
    return (
        "No contact address is set for this site: open an issue on the "
        f'<a href="{REPO_URL}/issues">GitHub repository</a>, or use GitHub private '
        "security reporting on the repository for anything you do not want public."
    )


def render(template: str, settings: Settings) -> str:
    public_url = (settings.public_url or "").strip().rstrip("/")
    if not public_url:
        template = "".join(
            line for line in template.splitlines(keepends=True) if "{{PUBLIC_URL}}" not in line
        )
    return (
        template.replace("{{PUBLIC_URL}}", _esc(public_url))
        .replace("{{CONTACT}}", contact_html(settings.contact_email))
        .replace("{{SIGNUPS}}", SIGNUPS_OPEN if settings.signups_enabled else SIGNUPS_CLOSED)
    )


def _template(name: str) -> str:
    return (resources.files("aliasfox") / "page_templates" / name).read_text(encoding="utf-8")


def install_pages(app: FastAPI, settings: Settings) -> None:
    """Register the public pages; multi mode only. Must run before the SPA
    catch-all (`install_web`). Signed-in visitors at `/` get the SPA index."""
    if settings.aliasfox_mode != "multi":
        return
    rendered = {path: render(_template(name), settings) for path, name in PAGES.items()}
    css = _template("site.css")
    html_headers = {"Cache-Control": NO_CACHE}

    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    def landing(request: Request):
        if has_valid_session(request):
            index = getattr(request.app.state, "spa_index", None)
            if index is None:
                raise HTTPException(status_code=404)
            return FileResponse(index, headers=html_headers)
        return HTMLResponse(rendered["/"], headers=html_headers)

    @app.api_route("/privacy", methods=["GET", "HEAD"], include_in_schema=False)
    def privacy():
        return HTMLResponse(rendered["/privacy"], headers=html_headers)

    @app.api_route("/terms", methods=["GET", "HEAD"], include_in_schema=False)
    def terms():
        return HTMLResponse(rendered["/terms"], headers=html_headers)

    @app.api_route("/site.css", methods=["GET", "HEAD"], include_in_schema=False)
    def site_css():
        return Response(css, media_type="text/css", headers={"Cache-Control": CSS_CACHE})
