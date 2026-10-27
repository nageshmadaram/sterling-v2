"""Additive schema for finite, media-only document access."""
def migrate(c):
    c.execute('''CREATE TABLE IF NOT EXISTS media_document_sessions (
        credential_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
        feature TEXT NOT NULL CHECK(feature IN ('tv','radio')), token_version INTEGER NOT NULL,
        expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS ix_media_session_user ON media_document_sessions(user_id,feature)')
