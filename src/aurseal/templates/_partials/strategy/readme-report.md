{% if ci == 'github' -%}
- **On GitHub Pages:** every push to `main` publishes both documents as web pages.
{% endif -%}
- **Locally:** read the Markdown files directly; `make check` fails on a missing section or
  a lint error and names it.
