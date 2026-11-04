import json
import pytest
from bili_remote.database import Database, SCHEMA, MIGRATIONS
from bili_remote.v2.storage import NamingMemoryRepository

@pytest.mark.parametrize('version',[1,2,3,4])
def test_additive_migration_backups_and_idempotency(tmp_path,version):
    db=Database(tmp_path/'test.sqlite3')
    with db.connect() as connection:
        connection.executescript('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT NOT NULL,applied_at TEXT NOT NULL);'+SCHEMA)
        connection.execute("INSERT INTO schema_migrations VALUES (1,'baseline','test')")
        for number,name,sql in MIGRATIONS:
            if number<=version:
                connection.executescript(sql)
                connection.execute('INSERT INTO schema_migrations VALUES (?,?,?)',(number,name,'test'))
        connection.execute("INSERT INTO settings(key,value) VALUES ('synthetic-existing','preserved')")
    db.initialize()
    assert db.fetch_one("SELECT value FROM settings WHERE key='synthetic-existing'")['value']=='preserved'
    assert db.fetch_one('SELECT max(version) v FROM schema_migrations')['v']==5
    NamingMemoryRepository(db).put('global',{}, {'selected_title':'Synthetic'},0)
    db.initialize()
    assert NamingMemoryRepository(db).get()['layers'][0]['selected_title']=='Synthetic'
    assert db.fetch_one('PRAGMA integrity_check')['integrity_check']=='ok'
    assert list((tmp_path/'backups').glob('bili-remote-schema-v5-*.sqlite3'))
