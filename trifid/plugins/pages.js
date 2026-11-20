// Serves the generated reader pages in build/pages/ to browsers. A request for the same IRI that
// prefers RDF (or names ?format=ttl and so on) falls through to Trifid's entity renderer unchanged.
import { readdir, readFile } from 'node:fs/promises';
import { join, relative, sep } from 'node:path';
import mimeparse from 'mimeparse';

const RDF = ['application/ld+json', 'application/n-quads', 'application/n-triples', 'application/rdf+xml',
  'application/trig', 'text/n3', 'text/turtle'];

const findPages = async (directory) => {
  const pages = new Map();
  for (const entry of await readdir(directory, { recursive: true, withFileTypes: true })) {
    if (!entry.isFile() || !entry.name.endsWith('.html')) continue;
    const file = join(entry.parentPath, entry.name);
    pages.set('/' + relative(directory, file).split(sep).join('/').replace(/\.html$/, ''), file);
  }
  return pages;
};

// Bound memory use even as the vocabulary grows. Immutable during this release.
const cache = new Map();
let cachedBytes = 0;
const MAX_BYTES = 32 * 1024 * 1024;
async function loadPage(file) {
  if (cache.has(file)) {
    const value = cache.get(file);
    cache.delete(file);
    cache.set(file, value);
    return value;
  }
  const value = await readFile(file, 'utf8');
  if (cache.has(file)) return cache.get(file);
  const size = Buffer.byteLength(value);
  if (size <= MAX_BYTES) {
    while (cachedBytes + size > MAX_BYTES && cache.size) {
      const key = cache.keys().next().value;
      cachedBytes -= Buffer.byteLength(cache.get(key));
      cache.delete(key);
    }
    cache.set(file, value);
    cachedBytes += size;
  }
  return value;
}

const editorial = async (path) => {
  const url = new URL('http://127.0.0.1:7880/content');
  url.searchParams.set('document', 'https://id.linkcite.org' + path);
  const started = performance.now();
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(100) });
    if (!response.ok) throw Error('editorial HTTP ' + response.status);
    const result = await response.json();
    return { content: result, ms: performance.now() - started, available: true };
  } catch {
    return { content: {}, ms: performance.now() - started, available: false };
  }
};

const insertEditorial = (html, content) => html.replace(
  /<!--LINKCITE-EDITORIAL:([A-Za-z0-9_=-]+)-->/g,
  (_, key) => {
    const iri = Buffer.from(key, 'base64url').toString('utf8');
    const value = Object.hasOwn(content, iri) ? content[iri] : '';
    // Only the read-only editorial service supplies sanitized, published HTML.
    return typeof value === 'string' && value ? '<section class="facet editorial">' + value + '</section>' : '';
  });

const wantsHtml = (request, query) => {
  const format = query.get('format');
  if (format) return format === 'html';
  // As in the entity renderer, RDF types come first, so a bare */* gets RDF.
  return mimeparse.bestMatch([...RDF, 'text/html'], `${request.headers.accept || 'text/html'}`) === 'text/html';
};

const factory = async ({ config, server, logger }) => {
  const pages = await findPages(config.directory);
  logger.info(`${pages.size} reader pages`);
  server.addHook('onRequest', async (request, reply) => {
    if (request.method !== 'GET' && request.method !== 'HEAD') return;
    const [path, search = ''] = request.url.split('?');
    const file = pages.get(path);
    if (!file || !wantsHtml(request, new URLSearchParams(search))) return;
    const started = performance.now();
    const [html, extra] = await Promise.all([loadPage(file), editorial(path)]);
    const page = insertEditorial(html, extra.content);
    reply.header('Vary', 'Accept')
      .header('Cache-Control', 'no-store')
      .header('Server-Timing', 'editorial;dur=' + extra.ms.toFixed(1) + ', origin;dur=' + (performance.now() - started).toFixed(1))
      .type('text/html; charset=utf-8');
    if (!extra.available) reply.header('X-LinkCite-Editorial', 'unavailable');
    return reply.send(page);
  });
};

export default factory;
