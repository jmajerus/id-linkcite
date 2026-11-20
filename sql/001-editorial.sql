-- Run as the database owner in a dedicated linkcite database.
-- Create distinct LOGIN roles linkcite_reader and linkcite_editor first.
-- Initial schema for an unprovisioned database; do not apply over the older draft schema.
BEGIN;
CREATE SCHEMA IF NOT EXISTS editorial;
REVOKE ALL ON SCHEMA editorial FROM PUBLIC;
CREATE TABLE editorial.resource_content (
    iri text PRIMARY KEY CHECK (iri LIKE 'https://id.linkcite.org/%' AND length(iri) <= 2048),
    document_iri text GENERATED ALWAYS AS (split_part(iri, '#', 1)) STORED,
    draft_markdown text NOT NULL DEFAULT '',
    private_markdown text NOT NULL DEFAULT '',
    published_markdown text,
    revision_number bigint NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    CHECK ((published_markdown IS NULL) = (published_at IS NULL)),
    CHECK (length(draft_markdown) <= 250000 AND length(private_markdown) <= 250000)
);
CREATE INDEX editorial_document_idx ON editorial.resource_content(document_iri)
    WHERE published_markdown IS NOT NULL;
CREATE TABLE editorial.resource_revision (
    iri text NOT NULL REFERENCES editorial.resource_content(iri),
    revision_number bigint NOT NULL,
    action text NOT NULL CHECK (action IN ('save', 'publish', 'unpublish', 'restore')),
    draft_markdown text NOT NULL,
    private_markdown text NOT NULL,
    published_markdown text,
    published_at timestamptz,
    changed_by text NOT NULL,
    changed_at timestamptz NOT NULL DEFAULT now(),
    change_note text,
    PRIMARY KEY (iri, revision_number)
);
-- Only public Markdown crosses the reader-role boundary. HTML is a derived,
-- sanitized cache entry in the reader service, never a writable database field.
CREATE VIEW editorial.public_content WITH (security_barrier=true) AS
    SELECT iri, document_iri, published_markdown
    FROM editorial.resource_content WHERE published_markdown IS NOT NULL;

-- All changes, including the history snapshot, occur under one row lock.
-- No caller can supply HTML, assign a different IRI, or rewrite history.
CREATE FUNCTION editorial.change_resource(
    p_iri text, p_expected bigint, p_action text,
    p_draft text, p_private text, p_restore bigint, p_actor text, p_note text
) RETURNS editorial.resource_content
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    current_row editorial.resource_content%ROWTYPE;
    old_revision editorial.resource_revision%ROWTYPE;
    new_draft text;
    new_private text;
    new_published text;
    new_published_at timestamptz;
BEGIN
    IF p_iri IS NULL OR p_iri NOT LIKE 'https://id.linkcite.org/%'
       OR length(p_iri) > 2048 OR p_expected IS NULL OR p_expected < 0
       OR p_action IS NULL OR p_action NOT IN ('save','publish','unpublish','restore')
       OR p_actor IS NULL OR length(p_actor) NOT BETWEEN 1 AND 200
       OR length(p_note) > 250000 THEN
        RAISE EXCEPTION 'Invalid editorial change' USING ERRCODE = '22023';
    END IF;
    INSERT INTO editorial.resource_content(iri) VALUES (p_iri) ON CONFLICT DO NOTHING;
    SELECT * INTO STRICT current_row FROM editorial.resource_content
        WHERE iri = p_iri FOR UPDATE;
    IF current_row.revision_number <> p_expected THEN
        RAISE EXCEPTION 'Stale editorial revision' USING ERRCODE = '40001';
    END IF;
    new_published := current_row.published_markdown;
    new_published_at := current_row.published_at;
    IF p_action = 'restore' THEN
        SELECT * INTO old_revision FROM editorial.resource_revision
            WHERE iri = p_iri AND revision_number = p_restore;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Revision not found' USING ERRCODE = 'P0002';
        END IF;
        new_draft := old_revision.draft_markdown;
        new_private := old_revision.private_markdown;
    ELSE
        IF p_draft IS NULL OR p_private IS NULL OR length(p_draft) > 250000
           OR length(p_private) > 250000 THEN
            RAISE EXCEPTION 'Invalid Markdown' USING ERRCODE = '22023';
        END IF;
        new_draft := p_draft;
        new_private := p_private;
    END IF;
    IF p_action = 'publish' THEN
        new_published := new_draft;
        IF new_published IS DISTINCT FROM current_row.published_markdown THEN
            new_published_at := now();
        END IF;
    ELSIF p_action = 'unpublish' THEN
        new_published := NULL;
        new_published_at := NULL;
    END IF;
    IF p_action <> 'restore' AND
       ROW(new_draft,new_private,new_published) IS NOT DISTINCT FROM
       ROW(current_row.draft_markdown,current_row.private_markdown,current_row.published_markdown) THEN
        RETURN current_row;
    END IF;
    UPDATE editorial.resource_content SET
        draft_markdown = new_draft, private_markdown = new_private,
        published_markdown = new_published, published_at = new_published_at,
        revision_number = p_expected + 1, updated_at = now()
        WHERE iri = p_iri RETURNING * INTO current_row;
    INSERT INTO editorial.resource_revision
        (iri,revision_number,action,draft_markdown,private_markdown,published_markdown,
         published_at,changed_by,change_note)
        VALUES (p_iri,current_row.revision_number,p_action,new_draft,new_private,
                new_published,new_published_at,p_actor,p_note);
    RETURN current_row;
END;
$$;
REVOKE ALL ON ALL TABLES IN SCHEMA editorial FROM PUBLIC, linkcite_reader, linkcite_editor;
REVOKE ALL ON FUNCTION editorial.change_resource(text,bigint,text,text,text,bigint,text,text) FROM PUBLIC;
GRANT USAGE ON SCHEMA editorial TO linkcite_reader, linkcite_editor;
GRANT SELECT ON editorial.public_content TO linkcite_reader;
GRANT SELECT ON editorial.resource_content, editorial.resource_revision TO linkcite_editor;
GRANT EXECUTE ON FUNCTION editorial.change_resource(text,bigint,text,text,text,bigint,text,text) TO linkcite_editor;
COMMIT;
