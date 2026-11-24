// Permanent redirects for vocabularies that moved, e.g. /patterns/... to /dehumanization/patterns/...
// Runs before the reader pages and the entity renderer, for browsers and RDF clients alike; a 301 keeps
// the request's Accept header, and the client keeps any #fragment. config.moves maps old path prefixes to new.

const factory = async ({ config, server, logger }) => {
  const moves = Object.entries(config.moves || {});
  logger.info(`${moves.length} moved path prefixes`);
  server.addHook('onRequest', async (request, reply) => {
    if (request.method !== 'GET' && request.method !== 'HEAD') return;
    const [path, ...rest] = request.url.split('?');
    for (const [from, to] of moves) {
      // Only whole segments: /patterns and /patterns/x move, /patterns-old does not.
      if (path === from || path.startsWith(from + '/')) {
        const search = rest.length ? '?' + rest.join('?') : '';
        return reply.header('Cache-Control', 'public, max-age=86400').redirect(to + path.slice(from.length) + search, 301);
      }
    }
  });
};

export default factory;
