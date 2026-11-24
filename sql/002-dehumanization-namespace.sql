-- Run once as the database owner, when deploying the release that moves the patterns and frameworks
-- vocabularies under their work: https://id.linkcite.org/patterns/... becomes
-- https://id.linkcite.org/dehumanization/patterns/..., and likewise for /frameworks/.
-- Moves each resource's editorial content and its whole revision history to the new IRI.
-- Fails, changing nothing, if a new IRI already has content. Running it again finds nothing to move.
--
-- Run it after deploying that release: the editor then rejects writes to the old IRIs, since they are
-- no longer in its registry. The lock below makes saves already in flight wait or finish first, so the
-- set of rows moved cannot change underneath the migration; readers are not blocked.
BEGIN;
LOCK TABLE editorial.resource_content, editorial.resource_revision IN SHARE ROW EXCLUSIVE MODE;
CREATE TEMP TABLE moved ON COMMIT DROP AS
    SELECT iri AS old_iri,
           'https://id.linkcite.org/dehumanization/' || substr(iri, length('https://id.linkcite.org/') + 1) AS new_iri
    FROM editorial.resource_content
    WHERE iri ~ '^https://id\.linkcite\.org/(patterns|frameworks)([/#]|$)';

-- The revision table references resource_content, so copy each row to its new IRI, repoint its
-- history, then remove the old row. A new IRI that already exists fails on the primary key.
INSERT INTO editorial.resource_content
    (iri, draft_markdown, private_markdown, published_markdown, revision_number, created_at, updated_at, published_at)
    SELECT m.new_iri, c.draft_markdown, c.private_markdown, c.published_markdown, c.revision_number,
           c.created_at, c.updated_at, c.published_at
    FROM editorial.resource_content c JOIN moved m ON c.iri = m.old_iri;
UPDATE editorial.resource_revision r SET iri = m.new_iri FROM moved m WHERE r.iri = m.old_iri;
DELETE FROM editorial.resource_content c USING moved m WHERE c.iri = m.old_iri;

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM editorial.resource_content
               WHERE iri ~ '^https://id\.linkcite\.org/(patterns|frameworks)([/#]|$)') THEN
        RAISE EXCEPTION 'Content remains at an old IRI; nothing was moved';
    END IF;
END $$;
SELECT count(*) AS moved_resources FROM moved;
COMMIT;
