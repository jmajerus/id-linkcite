# Editing resource landing pages

The semantic source repositories continue to own definitions, classifications,
relationships and identifiers. This repository owns supplementary prose for readers.

## Add or edit a published snippet

Each nonempty Markdown file under `site/content/iri/` belongs to an existing
public IRI. Strip the site's base URL and append `.md` to locate the file:

| Resource | File relative to site/content/iri/ |
|---|---|
| https://id.linkcite.org/patterns/roach-motel | patterns/roach-motel.md |
| https://id.linkcite.org/frameworks/df1 | frameworks/df1.md |
| https://id.linkcite.org/patterns/tags/context/bureaucracy | patterns/tags/context/bureaucracy.md |
| https://id.linkcite.org/frameworks/scheme | frameworks/scheme.md |
| https://id.linkcite.org/patterns/vocab#AntiPattern | patterns/vocab#AntiPattern.md |

The final example uses a literal hash in the filename on disk; the browser
resolves that resource as a section anchored within its vocabulary document.
The mapping currently accepts simple ASCII path segments and fragment names
(letters, digits, dots, underscores, hyphens and tildes, with an initial letter
or digit). An unknown or unpublished IRI, or an unsafe filename, fails the
build. When consolidating provisional identifiers, move or remove their Markdown
at the same time. There is no required file for any resource.

Write in ordinary Markdown, starting with level-two headings (the page provides
the title). Paragraphs, lists, tables, code, blockquotes, links and images are
supported. Relative image URLs must point to explicitly deployed public assets
under `site/assets/` (e.g. `/assets/figure.svg`); do not link private material.
Scripts, unsafe URLs, forms, inline styles and event handlers are stripped from
editorial HTML. Mermaid execution and arbitrary embeds are not enabled.

## Review and publish

Only reviewed public content belongs under `site/content/iri/`. Keep private
drafts outside that directory; a build treats every Markdown file within it as
approved for public HTML. Git commits/PR review provide editorial history. There
is no browser editor or write API yet. Author locally or use GitHub's file editor.
A save or commit alone does not deploy the site.

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests
python scripts/build.py
# Review generated build/pages/ and build/public.nq before deployment.
python tools/deploy.py
```

The build includes exact available RDF definitions, descriptions, scope notes
and comments in English (or untagged literals) in the reader pages; duplicate
literals are shown once. When a resource lacks a definition, no substitute is
invented and no publication placeholder is displayed. Supplemental Markdown is
HTML only; content negotiation and SPARQL expose the authoritative filtered RDF,
not the Markdown. Otherwise generic resources get a custom reader page only
when a snippet exists (hash IRIs get sections within their document).

The RDF allowlist still excludes full manuscript passages, private editorial
notes and source locators. Adding text to the editorial directory deliberately
publishes that text, independent of the RDF filter. Reader-page noindex
metadata remains independent of the decision to publish definitions.
