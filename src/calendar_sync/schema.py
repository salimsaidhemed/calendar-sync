"""Ordered migrations; each version is applied in one transaction."""

MIGRATIONS = (
    (
        """CREATE TABLE configurations (
            id TEXT PRIMARY KEY NOT NULL,
            owner_subject TEXT NOT NULL,
            google_calendar_id TEXT NOT NULL,
            nextcloud_calendar_url TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        )""",
        """CREATE TABLE credential_references (
            configuration_id TEXT NOT NULL REFERENCES configurations(id),
            provider TEXT NOT NULL CHECK(provider IN ('google', 'nextcloud')),
            file_path TEXT NOT NULL CHECK(substr(file_path,1,1) = '/'),
            PRIMARY KEY(configuration_id, provider)
        )""",
        """CREATE TABLE runs (
            id TEXT PRIMARY KEY NOT NULL,
            configuration_id TEXT NOT NULL REFERENCES configurations(id),
            dry_run INTEGER NOT NULL CHECK(dry_run IN (0,1)),
            status TEXT NOT NULL DEFAULT 'running'
                CHECK(status IN ('running','succeeded','failed','interrupted')),
            started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            finished_at TEXT,
            UNIQUE(configuration_id, id)
        )""",
        """CREATE TABLE mappings (
            configuration_id TEXT NOT NULL REFERENCES configurations(id),
            source_event_id TEXT NOT NULL,
            destination_href TEXT NOT NULL,
            destination_uid TEXT NOT NULL,
            source_fingerprint TEXT NOT NULL,
            destination_etag TEXT NOT NULL,
            applied_run_id TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            PRIMARY KEY(configuration_id, source_event_id),
            UNIQUE(configuration_id, destination_href),
            UNIQUE(configuration_id, destination_uid),
            FOREIGN KEY(configuration_id, applied_run_id)
                REFERENCES runs(configuration_id, id)
        )""",
        """CREATE TABLE audit_entries (
            id INTEGER PRIMARY KEY,
            configuration_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            action TEXT NOT NULL CHECK(action IN
                ('create','update','unchanged','conflict','unsupported','missing','run')),
            outcome TEXT NOT NULL CHECK(outcome IN
                ('planned','applied','skipped','failed','succeeded','interrupted')),
            source_event_id TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            FOREIGN KEY(configuration_id, run_id) REFERENCES runs(configuration_id, id)
        )""",
        "CREATE INDEX audit_by_configuration ON audit_entries(configuration_id, id)",
        """CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_entries
            BEGIN SELECT RAISE(ABORT, 'audit entries are append-only'); END""",
        """CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_entries
            BEGIN SELECT RAISE(ABORT, 'audit entries are append-only'); END""",
        """CREATE TRIGGER mapping_live_insert BEFORE INSERT ON mappings
            WHEN NOT EXISTS (SELECT 1 FROM runs WHERE id = NEW.applied_run_id
                AND configuration_id = NEW.configuration_id AND dry_run = 0
                AND status = 'running')
            BEGIN SELECT RAISE(ABORT, 'mapping requires an active live run'); END""",
        """CREATE TRIGGER mapping_live_update BEFORE UPDATE ON mappings
            WHEN NOT EXISTS (SELECT 1 FROM runs WHERE id = NEW.applied_run_id
                AND configuration_id = NEW.configuration_id AND dry_run = 0
                AND status = 'running')
            BEGIN SELECT RAISE(ABORT, 'mapping requires an active live run'); END""",
    ),
    (
        "ALTER TABLE runs ADD COLUMN purpose TEXT NOT NULL DEFAULT 'manual' CHECK(purpose IN ('manual','execution'))",
        """CREATE TABLE write_intents (
            id TEXT PRIMARY KEY NOT NULL,
            configuration_id TEXT NOT NULL REFERENCES configurations(id),
            source_event_id TEXT NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('create','update')),
            destination_href TEXT NOT NULL,
            destination_uid TEXT NOT NULL,
            source_fingerprint TEXT NOT NULL,
            owner_marker TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            stamp TEXT NOT NULL,
            expected_etag TEXT,
            baseline_fingerprint TEXT,
            origin_run_id TEXT NOT NULL,
            completed_run_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','applied','conflict')),
            attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
            reason_code TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            FOREIGN KEY(configuration_id,origin_run_id) REFERENCES runs(configuration_id,id),
            FOREIGN KEY(configuration_id,completed_run_id) REFERENCES runs(configuration_id,id),
            CHECK((action='create' AND expected_etag IS NULL AND baseline_fingerprint IS NULL)
               OR (action='update' AND expected_etag IS NOT NULL AND baseline_fingerprint IS NOT NULL))
        )""",
        "CREATE UNIQUE INDEX pending_write_source ON write_intents(configuration_id,source_event_id) WHERE status='pending'",
        "ALTER TABLE audit_entries ADD COLUMN reason_code TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE audit_entries ADD COLUMN operation_id TEXT REFERENCES write_intents(id)",
        """CREATE TRIGGER audit_operation_scope BEFORE INSERT ON audit_entries
            WHEN NEW.operation_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM write_intents WHERE id=NEW.operation_id AND configuration_id=NEW.configuration_id)
            BEGIN SELECT RAISE(ABORT, 'audit operation scope mismatch'); END""",
        """CREATE TRIGGER intent_live_insert BEFORE INSERT ON write_intents
            WHEN NOT EXISTS (SELECT 1 FROM runs WHERE id=NEW.origin_run_id
                AND configuration_id=NEW.configuration_id AND dry_run=0
                AND status='running' AND purpose='execution')
            BEGIN SELECT RAISE(ABORT, 'intent requires active live execution'); END""",
        """CREATE TRIGGER intent_identity_immutable BEFORE UPDATE OF id,configuration_id,source_event_id,
            action,destination_href,destination_uid,source_fingerprint,owner_marker,payload_hash,
            stamp,expected_etag,baseline_fingerprint,origin_run_id ON write_intents
            BEGIN SELECT RAISE(ABORT, 'intent identity is immutable'); END""",
        """CREATE TRIGGER intent_no_delete BEFORE DELETE ON write_intents
            BEGIN SELECT RAISE(ABORT, 'write intents are retained'); END""",
    ),
)
