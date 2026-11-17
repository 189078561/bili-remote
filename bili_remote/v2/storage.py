"""Durable rule, provenance and file-operation storage in the existing SQLite DB."""
from __future__ import annotations
import json
from .naming.errors import NamingError
from .naming.memory import NamingMemoryService, identity_key, validate_memory_rules
from ..database import utc_now


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


class NamingMemoryRepository:
    def __init__(self, database):
        self.db = database

    def get(self, identity=None, season=None):
        identity = identity_key(identity) if identity != 'global' else None
        rows = self.db.fetch_all('SELECT * FROM naming_memory WHERE scope=\'global\' OR '
            '(identity_key=? AND (scope=\'series\' OR (scope=\'season\' AND season_key=?))) '
            'ORDER BY CASE scope WHEN \'global\' THEN 0 WHEN \'series\' THEN 1 ELSE 2 END',
            (identity, season))
        return {'layers': [self._decode(row) for row in rows]}

    @staticmethod
    def _decode(row):
        row = dict(row)
        row.update(json.loads(row.pop('rules_json')))
        return row

    def put(self, scope, key, rules, expected_revision):
        # Accept the SubtitleRuleMemory adapter's stable string key as well.
        if isinstance(key, str):
            identity, sep, season = key.partition('#season:')
            key = {'identity_key': identity, 'season_key': int(season) if sep else -1}
        # Reuse the canonical key/rule validation without recursive persistence.
        class Validated:
            def put(_, s, k, r, rev):
                return s, k, r, rev
        scope, key, rules, expected_revision = NamingMemoryService(Validated()).put(scope, key, rules, expected_revision)
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old = conn.execute('SELECT * FROM naming_memory WHERE scope=? AND identity_key=? AND season_key=?',
                               (scope, key['identity_key'], key['season_key'])).fetchone()
            if (old['revision'] if old else 0) != expected_revision:
                raise NamingError('revision_conflict', '规则已由其他请求修改，请重新读取', status=409)
            # Patches from the subtitle adapter must not erase naming preferences.
            merged = json.loads(old['rules_json']) if old else {}
            merged.update(rules)
            validate_memory_rules(merged)
            stamp = utc_now()
            conn.execute('INSERT INTO naming_memory(scope,identity_key,season_key,rules_json,revision,created_at,updated_at) '
                'VALUES (?,?,?,?,?,?,?) ON CONFLICT(scope,identity_key,season_key) DO UPDATE SET '
                'rules_json=excluded.rules_json,revision=excluded.revision,updated_at=excluded.updated_at',
                (scope, key['identity_key'], key['season_key'], encode(merged), expected_revision+1, stamp, stamp))
            row = conn.execute('SELECT * FROM naming_memory WHERE scope=? AND identity_key=? AND season_key=?',
                               (scope, key['identity_key'], key['season_key'])).fetchone()
            return self._decode(row)


class RenameAuditRepository:
    def __init__(self, database):
        self.db = database

    def create(self, batch):
        self._write(batch, create=True)

    def update(self, batch):
        self._write(batch, create=False)

    def _write(self, batch, *, create):
        # Store only execution audit fields. Context/generated publication text never enters here.
        with self.db.connect() as conn:
            if create:
                conn.execute('INSERT INTO v2_rename_batches(id,owner,status,undo_of,created_at) VALUES (?,?,?,?,?)',
                             (batch['id'], batch['owner'], batch['status'], batch.get('undo_of'), utc_now()))
            else:
                conn.execute('UPDATE v2_rename_batches SET status=?,error=?,finished_at=? WHERE id=?',
                    (batch['status'], batch.get('error'), utc_now() if batch['status'] in {'completed','rolled_back','failed'} else None, batch['id']))
            for item in batch['items']:
                source = item['source']
                conn.execute('INSERT INTO v2_rename_items(batch_id,ordinal,root_key,source_path,kind,temporary_path,target_path,'
                    'before_fingerprint,after_fingerprint,status,intent,error,sidecar_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) '
                    'ON CONFLICT(batch_id,ordinal) DO UPDATE SET after_fingerprint=excluded.after_fingerprint,'
                    'status=excluded.status,intent=excluded.intent,error=excluded.error',
                    (batch['id'], item['ordinal'], source['root_key'], source['relative_path'], source.get('kind','file'),
                     item['temporary_path'], item['target_relative_path'], item['source_fingerprint'],
                     item.get('after_fingerprint'), item['status'], item.get('intent'), item.get('error'), encode(item.get('sidecar_of'))))

    def get(self, batch_id):
        batch = self.db.fetch_one('SELECT * FROM v2_rename_batches WHERE id=?', (batch_id,))
        if not batch:
            return None
        batch['items'] = []
        for row in self.db.fetch_all('SELECT * FROM v2_rename_items WHERE batch_id=? ORDER BY ordinal', (batch_id,)):
            batch['items'].append({'ordinal': row['ordinal'], 'source': {'root_key': row['root_key'],
                'relative_path': row['source_path'], 'kind': row['kind'], 'fingerprint': row['before_fingerprint']},
                'temporary_path': row['temporary_path'], 'target_relative_path': row['target_path'],
                'source_fingerprint': row['before_fingerprint'], 'after_fingerprint': row['after_fingerprint'],
                'status': row['status'], 'intent': row['intent'], 'error': row['error'],
                'sidecar_of': json.loads(row['sidecar_json']), 'temporary_required': True, 'warnings': []})
        return batch


class DownloadOriginRepository:
    def __init__(self, database):
        self.db = database

    def put(self, origin):
        ref = origin['original_resource']
        self.db.execute('INSERT INTO download_resource_origins(job_id,root_key,relative_path,fingerprint,kind,'
            'bvid,season_id,ep_id,cid,original_title,episode_index,api_mode,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) '
            'ON CONFLICT(job_id,root_key,relative_path) DO NOTHING',
            (origin['job_id'], ref['root_key'], ref['relative_path'], ref['fingerprint'], ref['kind'],
             *(origin.get(k) for k in ('bvid','season_id','ep_id','cid','original_title','episode_index')),
             origin.get('api_mode','web'), utc_now()))

    def get(self, job_id, ref):
        if not isinstance(ref, dict):
            return None
        row = self.db.fetch_one('SELECT * FROM download_resource_origins WHERE job_id=? AND root_key=? AND relative_path=?',
                               (job_id, ref.get('root_key'), ref.get('relative_path')))
        return self._decode(row) if row else None

    def probes_for(self, origins):
        """Read each downloaded job's evidence once per handoff."""
        jobs = {}
        result = {}
        for origin in origins:
            job_id = origin["job_id"]
            if job_id not in jobs:
                naming = self.db.get_download_naming(job_id)
                jobs[job_id] = {item["fingerprint"]: item for item in (naming or {}).get("outputs", [])}
            ref = origin["original_resource"]
            item = jobs[job_id].get(ref["fingerprint"])
            if not item or not item["path"].endswith("/" + ref["relative_path"]):
                continue
            probe = item.get("probe")
            if probe and not probe.get("probe_error"):
                result[(ref["root_key"], ref["relative_path"])] = {
                    key: {**val, "source_ref": ref["relative_path"]} if isinstance(val, dict) else val
                    for key, val in probe.items()
                }
        return result

    @staticmethod
    def _decode(row):
        return {**{k: row[k] for k in ('job_id','bvid','season_id','ep_id','cid','original_title','episode_index','api_mode')},
            'original_resource': {k: row[k] for k in ('root_key','relative_path','fingerprint','kind')}}

    def list(self, job_id):
        return [self._decode(row) for row in self.db.fetch_all('SELECT * FROM download_resource_origins WHERE job_id=?', (job_id,))]
