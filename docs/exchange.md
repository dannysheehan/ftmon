# Publish FTMON Exchange

FTMON Exchange is the static, curated view of the recipes in
`extra-monitors/`. It improves discovery without becoming a software upload
service: contributors submit reviewed pull requests, third-party executables
remain with their upstream projects, and publication is compatibility evidence
rather than endorsement or a security audit.

The production address is <https://exchange.ftmon.org/>. The generated site has
no server-side application, account database, analytics or external assets.

## Add or update a recipe

Copy `extra-monitors/_template/`, choose a stable lowercase ID and complete its
article, metadata, registry example, monitor definition and protocol fixtures.
Publication metadata has three additional responsibilities:

- `category` is one of the bounded catalogue categories;
- `tags` is a sorted, unique list of lowercase discovery terms; and
- `min_ftmon_version` is the first FTMON version that understands the recipe.

The bounded `privilege` value is `none`, `service-socket`, or `sudo-wrapper`.
`service-socket` identifies a local socket the daemon identity can already
access; it never authorizes changing groups or socket modes. `sudo-wrapper` is
the advanced exception documented for custom units without the shipped
`NoNewPrivileges=yes` hardening.

Commands belong only in `checks.toml.example`. The publisher displays them as
escaped text and never runs them. It also refuses recipe symlinks, unsafe URL
schemes and unbounded or malformed metadata because a documentation build must
not become a pull-request code-execution path.

Validate before opening a pull request:

```sh
uv run ruff check tools/build_exchange.py tests/exchange tests/extra_monitors
uv run pytest -q tests/exchange tests/extra_monitors
uv run python tools/build_exchange.py --output dist/exchange
```

Preview without adding generated files to Git:

```sh
uv run python -m http.server 8000 --directory dist/exchange
```

Open <http://127.0.0.1:8000/>. `dist/` is ignored because source recipes,
templates, assets and the generator—not generated HTML—are the authority.

## GitHub Pages setup

The `FTMON Exchange` workflow builds on relevant pull requests and pushes. A
pull request has read-only repository permission and cannot upload or deploy a
Pages artifact. Only a push to protected `main` uploads the tested artifact;
the dependent deploy job alone receives `pages: write` and `id-token: write`
inside the `github-pages` environment.

One repository administrator must perform these initial settings:

1. In repository **Settings → Pages**, select **GitHub Actions** as the source.
2. Protect the `github-pages` environment and restrict deployment branches to
   `main`.
3. In the GitHub account's **Settings → Pages**, verify `ftmon.org` using the
   DNS TXT challenge and retain that record. Verification prevents another
   GitHub account claiming an orphaned subdomain.
4. In repository **Settings → Pages**, set the custom domain to
   `exchange.ftmon.org`.
5. At the DNS provider, add `exchange.ftmon.org CNAME dannysheehan.github.io.`
   Do not use a wildcard record and do not point the subdomain at the repository
   path.
6. After DNS and certificate provisioning complete, enable **Enforce HTTPS**.

GitHub's custom-workflow Pages deployment uses the repository setting as the
custom-domain authority; the generated `CNAME` is also retained as visible and
testable deployment intent.

## Verify a deployment

After the workflow succeeds:

```sh
dig exchange.ftmon.org CNAME +short
curl --fail --show-error --location https://exchange.ftmon.org/
curl --fail --show-error https://exchange.ftmon.org/search-index.v1.json
curl --fail --show-error https://exchange.ftmon.org/recipes/http-tls/
```

Confirm the certificate covers `exchange.ftmon.org`, search works, every card
is still visible with JavaScript disabled, and the workflow deployment URL is
the expected custom domain. The normal CI link test proves generated local
links without depending on DNS or GitHub Pages availability.

## Roll back or disable

The site contains no mutable data. To roll back content, revert the responsible
commit on `main`; the workflow publishes the previous deterministic result. To
stop publication, disable the Pages site first and immediately remove the DNS
record. Leaving a DNS record pointed at a disabled Pages site creates a domain
takeover risk even when the repository itself is private or deleted.

Do not replace the Pages artifact manually. A manual upload would bypass the
recipe tests and make the public output differ from reviewed repository state.
