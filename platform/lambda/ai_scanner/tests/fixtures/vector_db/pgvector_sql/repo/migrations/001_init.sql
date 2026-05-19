-- bootstrap pgvector

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE embeddings (
    id   uuid PRIMARY KEY,
    vec  vector(1536) NOT NULL
);
