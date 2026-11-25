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

import hashlib
import html
import json
import re
from importlib import resources

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response

from aliasfox.auth import has_valid_session
from aliasfox.config import Settings
from aliasfox.web import IMMUTABLE, NO_CACHE

REPO_URL = "https://github.com/codecrumb/aliasfox"
_PLACEHOLDER = re.compile(r"\{\{(PUBLIC_URL|CONTACT|SIGNUPS)\}\}")
PAGES = {"/": "landing.html", "/privacy": "privacy.html", "/terms": "terms.html"}

SIGNUPS_OPEN = '<a class="button primary" href="/signup">Sign up</a>'
SIGNUPS_CLOSED = '<p class="notice">Sign-ups are closed for now.</p>'

# The header link on privacy/terms; swapped per visitor (signed in -> the app,
# signed out -> sign up while sign-ups are open).
HEADER_LOGIN = '<a class="header-link" href="/login">Log in</a>'
HEADER_SIGNUP = '<a class="header-link" href="/signup">Sign up</a>'
HEADER_APP = '<a class="header-link" href="/">Continue to app</a>'


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
    values = {
        "PUBLIC_URL": _esc(public_url),
        "CONTACT": contact_html(settings.contact_email),
        "SIGNUPS": SIGNUPS_OPEN if settings.signups_enabled else SIGNUPS_CLOSED,
    }
    # One pass: a configured value that itself contains "{{CONTACT}}" etc. is
    # never expanded again.
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


def _template(name: str) -> str:
    return (resources.files("aliasfox") / "page_templates" / name).read_text(encoding="utf-8")


ROBOTS_SINGLE = "User-agent: *\nDisallow: /\n"  # a private instance: nothing to index
ROBOTS_MULTI = "User-agent: *\nDisallow: /api/\nDisallow: /login\nDisallow: /signup\n"

LLMS_TXT = """# AliasFox

> AliasFox is a self-hosted service for managing your iCloud "Hide My Email" aliases: list, search, generate, label, deactivate and delete them from your own server. It runs as a Docker Compose service.

## Resources

- [AliasFox on GitHub](https://github.com/codecrumb/aliasfox): the repository, quick start and configuration reference.
- [Live demo](/demo): try the app with sample data, no account needed.
- [Privacy policy](/privacy): how the site treats your data.
- [Terms](/terms): usage terms of the service.
"""

# Passes Lighthouse's `llms-txt` audit (domain-root /llms.txt: H1, a Markdown
# link, >= 50 chars). Entries with an `identifier`, `displayName`, `type` and
# `url`/`data` go here once the service exposes agentic resources (MCP
# servers, A2A agents, agent skills).
ARD_MANIFEST = {"specVersion": "1.0", "entries": []}


def install_pages(app: FastAPI, settings: Settings) -> None:
    """Register robots.txt, and in multi mode the public pages. Must run before
    the SPA catch-all (`install_web`). Signed-in visitors at `/` get the SPA
    index; at `/login` and `/signup` they are redirected to `/`."""
    multi = settings.aliasfox_mode == "multi"
    base = (settings.public_url or "").strip().rstrip("/")
    sitemap = bool(multi and base)  # sitemaps need absolute URLs
    robots_txt = ROBOTS_MULTI if multi else ROBOTS_SINGLE
    if sitemap:
        robots_txt += f"Sitemap: {base}/sitemap.xml\n"

    @app.api_route("/robots.txt", methods=["GET", "HEAD"], include_in_schema=False)
    def robots():
        return PlainTextResponse(robots_txt)

    @app.api_route("/llms.txt", methods=["GET", "HEAD"], include_in_schema=False)
    def llms_txt():
        return PlainTextResponse(LLMS_TXT, headers={"Cache-Control": NO_CACHE})

    @app.api_route("/.well-known/ard.json", methods=["GET", "HEAD"], include_in_schema=False)
    def ard_manifest():
        return PlainTextResponse(
            json.dumps(ARD_MANIFEST, indent=2) + "\n",
            media_type="application/json",
            headers={"Cache-Control": NO_CACHE},
        )

    if sitemap:
        urls = "".join(f"<url><loc>{_esc(base + p)}</loc></url>" for p in ("/", "/demo", "/privacy", "/terms"))
        sitemap_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
        )

        @app.api_route("/sitemap.xml", methods=["GET", "HEAD"], include_in_schema=False)
        def sitemap_route():
            return Response(sitemap_xml, media_type="application/xml")

    if not multi:
        return
    # Content-hashed URLs, cached forever: a change in content is a new URL.
    # hero3d first, because hero.js imports it by name.
    assets: dict[str, tuple[str, str]] = {}

    def publish(name: str, body: str, media_type: str) -> str:
        stem, ext = name.rsplit(".", 1)
        url = f"/{stem}.{hashlib.sha256(body.encode()).hexdigest()[:10]}.{ext}"
        assets[url] = (body, media_type)
        return url

    js, css = "text/javascript", "text/css"
    hero_3d_url = publish("hero3d.js", _template("hero3d.js"), js)
    hero_url = publish("hero.js", _template("hero.js").replace("/hero3d.js", hero_3d_url), js)
    css_url = publish("site.css", _template("site.css"), css)
    urls = {"/site.css": css_url, "/hero.js": hero_url}
    rendered = {}
    for path, name in PAGES.items():
        page = render(_template(name), settings)
        for plain, hashed in urls.items():
            page = page.replace(f'"{plain}"', f'"{hashed}"')
        rendered[path] = page
    html_headers = {"Cache-Control": NO_CACHE}

    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    def landing(request: Request):
        if has_valid_session(request):
            index = getattr(request.app.state, "spa_index", None)
            if index is None:
                raise HTTPException(status_code=404)
            return FileResponse(index, headers=html_headers)
        return HTMLResponse(rendered["/"], headers=html_headers)

    def auth_screen(request: Request):
        if has_valid_session(request):
            return RedirectResponse("/", status_code=302)
        index = getattr(request.app.state, "spa_index", None)
        if index is None:
            raise HTTPException(status_code=404)
        return FileResponse(index, headers=html_headers)

    for path in ("/login", "/signup"):
        app.add_api_route(path, auth_screen, methods=["GET", "HEAD"], include_in_schema=False)

    out_link = HEADER_SIGNUP if settings.signups_enabled else HEADER_LOGIN

    def legal(path: str):
        signed_out = rendered[path].replace(HEADER_LOGIN, out_link)
        signed_in = rendered[path].replace(HEADER_LOGIN, HEADER_APP)

        def view(request: Request):
            return HTMLResponse(signed_in if has_valid_session(request) else signed_out, headers=html_headers)

        app.add_api_route(path, view, methods=["GET", "HEAD"], include_in_schema=False)

    legal("/privacy")
    legal("/terms")

    for url, (body, media_type) in assets.items():
        def serve(body: str = body, media_type: str = media_type):
            return Response(body, media_type=media_type, headers={"Cache-Control": IMMUTABLE})

        app.add_api_route(url, serve, methods=["GET", "HEAD"], include_in_schema=False)
