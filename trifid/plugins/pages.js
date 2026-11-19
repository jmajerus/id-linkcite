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
    reply.header('Vary', 'Accept').type('text/html; charset=utf-8');
    return reply.send(await readFile(file));
  });
};

export default factory;
