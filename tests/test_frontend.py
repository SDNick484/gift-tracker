"""The shell around the frontend: what GET / and GET /manifest.json say, and static checks over static/ that keep
the Content-Security-Policy (no inline script, no inline style) and the escaping rule true as screens are added.
The JavaScript's own unit tests are tests/frontend.test.mjs (`node --test tests/frontend.test.mjs`)."""
import glob
import os
import re

import pytest

import settings
from conftest import LAN_IP, auth

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "static")
JS_FILES = sorted(glob.glob(os.path.join(STATIC, "js", "*.js")))

# The only things a template-literal interpolation that builds markup may be: one call to one of these.
SAFE_CALL = re.compile(r"(esc|money|daysLabel|render[A-Za-z0-9_]*)\(")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- what the server renders
def test_index_title_and_manifest_use_app_name_only(client):
    html = client.get("/", headers=auth()).get_data(as_text=True)
    assert "<title>Ledger</title>" in html
    m = client.get("/manifest.json", headers=auth()).get_json()
    assert (m["name"], m["short_name"]) == ("Ledger", "Ledger")
    assert "__APP_NAME__" not in html


def test_app_name_setting_reaches_title_home_screen_name_and_manifest_escaped(client, monkeypatch):
    monkeypatch.setattr(settings, "APP_NAME", 'Tab <b>"&</b>')
    html = client.get("/", headers=auth()).get_data(as_text=True)
    assert "<title>Tab &lt;b&gt;&quot;&amp;&lt;/b&gt;</title>" in html
    assert '<meta name="apple-mobile-web-app-title" content="Tab &lt;b&gt;&quot;&amp;&lt;/b&gt;">' in html
    assert "<b>" not in html
    m = client.get("/manifest.json", headers=auth()).get_json()
    assert (m["name"], m["short_name"]) == ('Tab <b>"&</b>', 'Tab <b>"&</b>')


def test_manifest_is_a_manifest_and_the_page_fetches_it_with_credentials(client):
    r = client.get("/manifest.json", headers=auth())
    assert r.mimetype == "application/manifest+json"
    m = r.get_json()
    assert m["start_url"] == "/" and m["display"] == "standalone" and m["icons"]
    html = client.get("/", headers=auth()).get_data(as_text=True)
    # The request guard covers /manifest.json too, and a manifest fetch carries no credentials by default.
    assert re.search(r'<link rel="manifest" href="/manifest.json" crossorigin="use-credentials">', html)


def test_index_manifest_and_assets_are_behind_the_identity_guard(client):
    for path in ("/", "/manifest.json", "/static/index.html", "/static/style.css", "/static/js/main.js",
                 "/static/icon.svg"):
        r = client.get(path, environ_base={"REMOTE_ADDR": LAN_IP})
        assert (r.status_code, r.get_json()) == (403, {"error": "Forbidden"}), path


def test_pages_and_assets_carry_the_csp_and_no_store(client):
    for path in ("/", "/manifest.json", "/static/style.css", "/static/js/main.js", "/static/js/core.js",
                 "/static/icon.svg"):
        r = client.get(path, headers=auth())
        assert r.status_code == 200, path
        csp = r.headers["Content-Security-Policy"]
        assert "script-src 'self'" in csp and "style-src 'self'" in csp and "unsafe" not in csp, path
        assert r.headers["Cache-Control"] == "no-store", path


def test_the_app_shell_names_nothing_about_gifts_or_people(client):
    """Page title, home-screen name, manifest and icon are neutral."""
    html = client.get("/", headers=auth()).get_data(as_text=True)
    head = html.split("</head>")[0]
    manifest = client.get("/manifest.json", headers=auth()).get_data(as_text=True)
    icon = read(os.path.join(STATIC, "icon.svg"))
    for text in (head, manifest, icon):
        assert not re.search(r"gift|present|santa|christmas|kid|wish", text, re.I), text


# ---------------------------------------------------------------- the CSP, held by construction
def test_no_inline_script_or_handlers():
    html = read(os.path.join(STATIC, "index.html"))
    assert "<script>" not in html and "onclick=" not in html
    assert not re.search(r"<script(?![^>]*\ssrc=)", html), "an inline <script>"
    assert not re.search(r"<[^>]+\son[a-z]+\s*=", html, re.I), "an inline event handler"
    assert not re.search(r"<[^>]+\sstyle\s*=", html, re.I) and "<style" not in html, "inline style"
    assert 'type="module"' in html and "/static/js/main.js" in html


@pytest.mark.parametrize("path", JS_FILES, ids=os.path.basename)
def test_scripts_never_write_inline_style_or_handlers_or_raw_html(path):
    src = read(path)
    assert not re.search(r"""\bstyle\s*=\s*["'\\]""", src), "a style attribute in a string"
    for banned in ("cssText", 'setAttribute("style"', "setAttribute('style'", "insertAdjacentHTML", "outerHTML",
                   "document.write", "eval(", "new Function"):
        assert banned not in src, banned
    assert not re.search(r"""\bon[a-z]+\s*=\s*["'\\]""", src), "an inline event handler attribute in a string"
    assert not re.search(r"""\.on[a-z]+\s*=[^=]""", src), "assign handlers with addEventListener"


# ---------------------------------------------------------------- escaping
def _skip_string(src, i):
    quote = src[i]
    i += 1
    while src[i] != quote:
        i += 2 if src[i] == "\\" else 1
    return i + 1


def _skip_comment(src, i):
    """i is at a "/": index after the comment starting here, or i when it does not start one."""
    if src.startswith("//", i):
        j = src.find("\n", i)
        return len(src) if j < 0 else j
    if src.startswith("/*", i):
        return src.index("*/", i) + 2
    return i


def _template(src, i, found):
    """src[i] is a backtick. Parse the template literal (and any nested in its ${} expressions); every one is
    appended to `found` as (pieces, exprs): the raw text segments and the ${} expression sources. Returns the
    index after the closing backtick."""
    i += 1
    pieces, exprs, buf = [], [], []
    while src[i] != "`":
        if src[i] == "\\":
            buf.append(src[i:i + 2])
            i += 2
        elif src.startswith("${", i):
            pieces.append("".join(buf))
            buf = []
            start, depth, i = i + 2, 1, i + 2
            while depth:
                c = src[i]
                if c in "'\"":
                    i = _skip_string(src, i)
                elif c == "`":
                    i = _template(src, i, found)
                elif c == "/" and (j := _skip_comment(src, i)) != i:
                    i = j
                else:
                    depth += (c == "{") - (c == "}")
                    i += 1
            exprs.append(src[start:i - 1])
        else:
            buf.append(src[i])
            i += 1
    pieces.append("".join(buf))
    found.append((pieces, exprs))
    return i + 1


def template_literals(src):
    found, i = [], 0
    while i < len(src):
        c = src[i]
        if c in "'\"":
            i = _skip_string(src, i)
        elif c == "`":
            i = _template(src, i, found)
        elif c == "/" and (j := _skip_comment(src, i)) != i:
            i = j
        else:
            i += 1
    return found


def is_single_safe_call(expr):
    """`esc(...)`, `money(...)`, `daysLabel(...)` or `renderXxx(...)` and nothing after the closing parenthesis."""
    expr = expr.strip()
    m = SAFE_CALL.match(expr)
    if not m:
        return False
    depth, i = 1, m.end()
    while i < len(expr):
        c = expr[i]
        if c in "'\"":
            i = _skip_string(expr, i)
            continue
        if c == "`":
            return False  # a template literal as an argument is built elsewhere, by a render function
        depth += (c == "(") - (c == ")")
        i += 1
        if depth == 0:
            return i == len(expr)
    return False


def test_the_scanner_itself():
    src = 'a = `<b>${esc(x)}</b> ${items.map(i => `<i>${i}</i>`).join("")} // not a comment`; // `x`\nb = "`"'
    found = template_literals(src)
    assert len(found) == 2
    outer = next(f for f in found if len(f[1]) == 2)
    assert [e.strip() for e in outer[1]] == ["esc(x)", 'items.map(i => `<i>${i}</i>`).join("")']
    assert is_single_safe_call("esc(a, b(c))") and is_single_safe_call(" renderRow(x) ")
    assert not is_single_safe_call("esc(a) + b") and not is_single_safe_call("x ? esc(a) : ''")
    assert not is_single_safe_call("escape(a)") and not is_single_safe_call("esc(a)(b)")
    assert not is_single_safe_call("esc(a) || `<x>`")


def test_unquoted_attribute_rule_ignores_query_strings():
    unquoted = re.compile(r"\s[A-Za-z_:][\w:.-]*=\s*$")
    assert unquoted.search('<a href=') and unquoted.search('<input value= ')
    assert not unquoted.search('<a href="#/idea?for=') and not unquoted.search('<a href="x" title="')


@pytest.mark.parametrize("path", JS_FILES, ids=os.path.basename)
def test_every_innerhtml_interpolation_is_escaped(path):
    """Every `${...}` in a template literal that holds markup is one call to esc, money, daysLabel or a function
    whose name starts with `render`, and never sits in an unquoted attribute. Every innerHTML assignment is a
    template literal, an empty string or a render* call."""
    src = read(path)
    for pieces, exprs in template_literals(src):
        if not re.search(r"</?[A-Za-z]", "".join(pieces)):
            continue  # not markup: a URL path, a message
        for before, expr in zip(pieces, exprs):
            assert is_single_safe_call(expr), f"{os.path.basename(path)}: unescaped interpolation ${{{expr.strip()}}}"
            # `name=${...}` with no quote: an attribute value that a space or > in the text would break out of
            assert not re.search(r"\s[A-Za-z_:][\w:.-]*=\s*$", before), \
                f"{os.path.basename(path)}: unquoted attribute before ${{{expr.strip()}}}"
    for m in re.finditer(r"innerHTML\s*\+?=\s*(?P<rhs>[^;\n]*)", src):
        rhs = m.group("rhs").strip()
        assert rhs.startswith(("`", "render")) or rhs in ('""', "''"), f"{os.path.basename(path)}: innerHTML = {rhs}"


def test_every_innerhtml_check_has_something_to_check():
    """Guards the guard: once screens exist, the scanner must be finding template literals in them."""
    if len(JS_FILES) > 3:
        assert sum(len(template_literals(read(p))) for p in JS_FILES) > 3


# ---------------------------------------------------------------- titles
def test_document_title_is_never_assigned_a_variable():
    for f in JS_FILES:
        src = read(f)
        if f.endswith("main.js"):
            for m in re.finditer(r"document\.title\s*=\s*([^;\n]+)", src):
                assert m.group(1).strip() == "state.me.app_name", m.group(1)  # APP_NAME, from /api/me
        else:
            assert "document.title" not in src, f
