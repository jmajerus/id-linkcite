// Answers /favicon.ico, which browsers and feed readers fetch without being told to. Pages name the
// icons in site/assets themselves; this keeps the conventional path from returning 404.
import { readFile } from 'node:fs/promises';

const factory = async ({ config }) => {
  const icon = await readFile(config.file);
  return {
    defaultConfiguration: async () => ({ methods: ['GET', 'HEAD'] }),
    routeHandler: async () => async (request, reply) => reply
      .type('image/x-icon')
      .header('Cache-Control', 'public, max-age=86400')
      .send(icon),
  };
};

export default factory;
