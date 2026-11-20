-- Run as the database owner in a dedicated linkcite database.
-- Create distinct LOGIN roles linkcite_reader and linkcite_editor first.
BEGIN;
CREATE SCHEMA IF NOT EXISTS editorial;
REVOKE ALL ON SCHEMA editorial FROM PUBLIC;
CREATE TABLE IF NOT EXISTS editorial.resource_content (
    iri text PRIMARY KEY CHECK (iri LIKE 'https://id.linkcite.org/%' AND length(iri) <= 2048),
    document_iri text GENERATED ALWAYS AS (split_part(iri, '#', 1)) STORED,
    draft_markdown text NOT NULL DEFAULT '',
    private_markdown text NOT NULL DEFAULT '',
    published_markdown text,
    published_html text,
    revision_number bigint NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    CHECK ((published_html IS NULL) = (published_markdown IS NULL)),
    CHECK ((published_markdown IS NULL) = (published_at IS NULL))
);
CREATE INDEX IF NOT EXISTS editorial_document_idx ON editorial.resource_content(document_iri)
    WHERE published_html IS NOT NULL;
CREATE TABLE IF NOT EXISTS editorial.resource_revision (
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
CREATE OR REPLACE VIEW editorial.public_content WITH (security_barrier=true) AS
    SELECT iri, document_iri, published_html
    FROM editorial.resource_content WHERE published_html IS NOT NULL;
REVOKE ALL ON ALL TABLES IN SCHEMA editorial FROM PUBLIC;
GRANT USAGE ON SCHEMA editorial TO linkcite_reader, linkcite_editor;
GRANT SELECT ON editorial.public_content TO linkcite_reader;
GRANT SELECT, INSERT, UPDATE ON editorial.resource_content TO linkcite_editor;
GRANT SELECT, INSERT ON editorial.resource_revision TO linkcite_editor;
COMMIT;
