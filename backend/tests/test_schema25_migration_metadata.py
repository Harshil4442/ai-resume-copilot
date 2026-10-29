"""Fresh production imports and local supplied-connection migrations only."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def fresh(script, *, extra=None):
    env = os.environ.copy()
    for name in ("DATABASE_URL", "APP_ENV", "PYTEST_CURRENT_TEST"):
        env.pop(name, None)
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "infra/gcp"), str(ROOT / "backend")))
    if extra:
        env.update(extra)
    return subprocess.run([sys.executable, "-c", script], cwd=ROOT / "backend",
                          env=env, text=True, capture_output=True, timeout=30)


def test_fresh_production_import_requires_runtime_database_configuration():
    result = fresh("""
try:
    import app.database
except RuntimeError as error:
    assert str(error) == 'DATABASE_URL must be configured in production.'
    print('production_runtime_configuration_required')
else:
    raise AssertionError('missing production database configuration accepted')
""")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "production_runtime_configuration_required"


PROOF_SETUP = """
import os
from pathlib import Path
from types import SimpleNamespace
from sqlalchemy.engine import URL
import schema25_migration_metadata as metadata
root = Path.cwd().parent
runtime = URL.create('postgresql+psycopg', username='synthetic_runtime',
    password='not-a-real-password', host='database.example.invalid', database='neondb',
    query={'sslmode':'verify-full'})
class Neon:
    FRESH_DATABASE_OWNER_OID = 6171
    @staticmethod
    def collect_schema21_release_target(observer, reference, **kwargs):
        assert reference == 'synthetic-runtime:2'
        assert kwargs == {'alias':'replacement_runtime','database_owner_oid':6171,
                          'expected_schema':'20261023_0021'}
        return object(), SimpleNamespace(_url=runtime)
observer = SimpleNamespace(_module=lambda *args: SimpleNamespace(neon_identity=Neon))
"""


def test_real_alembic_metadata_uses_supplied_connection_without_runtime_connect(tmp_path):
    database = tmp_path / "supplied.db"
    prepare = fresh("from alembic import command; from alembic.config import Config; "
                    "command.upgrade(Config('alembic.ini'),'20261023_0021')",
                    extra={"APP_ENV": "test", "DATABASE_URL": f"sqlite:///{database}"})
    assert prepare.returncode == 0, prepare.stderr
    result = fresh(PROOF_SETUP + """
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
engine = create_engine(os.environ['SYNTHETIC_SUPPLIED_URL'])
extra_connections = []
with engine.connect() as connection, connection.begin():
    original_connect = Engine.connect
    def no_second_connection(self, *args, **kwargs):
        extra_connections.append(True)
        raise AssertionError('unexpected second database connection')
    Engine.connect = no_second_connection
    try:
        with metadata.initialized_metadata(root, observer, 'synthetic-runtime:2'):
            assert os.environ['APP_ENV'] == 'production'
            configuration = Config('alembic.ini')
            configuration.attributes['connection'] = connection
            command.upgrade(configuration,'20261028_0025')
            assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '20261028_0025'
    finally:
        Engine.connect = original_connect
assert extra_connections == []
assert 'DATABASE_URL' not in os.environ and 'APP_ENV' not in os.environ
engine.dispose()
print('supplied_connection_only_environment_restored')
""", extra={"SYNTHETIC_SUPPLIED_URL": f"sqlite:///{database}"})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "supplied_connection_only_environment_restored"
    assert "not-a-real-password" not in result.stdout + result.stderr


def test_unused_runtime_engine_refuses_connect_and_restores_environment_on_failure():
    result = fresh(PROOF_SETUP + """
import rolling_release
try:
    with metadata.initialized_metadata(root, observer, 'synthetic-runtime:2'):
        import app.database
        app.database.engine.connect()
except rolling_release.RollingDenied as error:
    assert str(error) == 'schema25_metadata_runtime_connection_refused'
else:
    raise AssertionError('unused runtime Engine connected')
assert 'DATABASE_URL' not in os.environ and 'APP_ENV' not in os.environ
try:
    app.database.engine.connect()
except rolling_release.RollingDenied as error:
    assert str(error) == 'schema25_metadata_runtime_connection_refused'
else:
    raise AssertionError('cached runtime Engine connected after metadata scope')
print('runtime_connect_refused_environment_restored')
""")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "runtime_connect_refused_environment_restored"
    assert "not-a-real-password" not in result.stdout + result.stderr


def test_environment_is_restored_even_if_unused_engine_disposal_fails():
    result = fresh(PROOF_SETUP + """
import sys
original_path = list(sys.path)
try:
    with metadata.initialized_metadata(root, observer, 'synthetic-runtime:2'):
        import app.database
        def disposal_failure():
            raise RuntimeError('synthetic disposal failure')
        app.database.engine.dispose = disposal_failure
except RuntimeError as error:
    assert str(error) == 'synthetic disposal failure'
else:
    raise AssertionError('disposal failure was hidden')
assert 'DATABASE_URL' not in os.environ and 'APP_ENV' not in os.environ
assert sys.path == original_path
print('environment_restored_despite_disposal_failure')
""")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "environment_restored_despite_disposal_failure"
    assert "not-a-real-password" not in result.stdout + result.stderr


def test_preloaded_application_database_refuses_before_secret_access():
    result = fresh("""
import sys
from pathlib import Path
from types import SimpleNamespace
import schema25_migration_metadata as metadata
import rolling_release
sys.modules['app.database'] = SimpleNamespace()
def forbidden(*args):
    raise AssertionError('secret read despite preloaded database')
try:
    with metadata.initialized_metadata(Path.cwd().parent, SimpleNamespace(_module=forbidden), 'synthetic-runtime:2'):
        raise AssertionError('incompatible module adopted')
except rolling_release.RollingDenied as error:
    assert str(error) == 'schema25_metadata_fresh_process_required'
print('preloaded_database_refused')
""")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "preloaded_database_refused"


def test_matching_operator_package_loads_exact_candidate_metadata_and_restores_path(tmp_path):
    result = fresh(PROOF_SETUP + """
import app
package = Path(os.environ['SYNTHETIC_PACKAGE'])
package.mkdir()
initializer = package/'__init__.py'
initializer.write_bytes((root/'backend/app/__init__.py').read_bytes())
app.__file__, app.__path__ = str(initializer), [str(package)]
with metadata.initialized_metadata(root, observer, 'synthetic-runtime:2'):
    import app.database, app.models
    assert Path(app.database.__file__).resolve() == root/'backend/app/database.py'
    assert Path(app.models.__file__).resolve() == root/'backend/app/models.py'
assert app.__path__ == [str(package)]
assert 'DATABASE_URL' not in os.environ and 'APP_ENV' not in os.environ
print('exact_candidate_metadata_operator_package_restored')
""", extra={"SYNTHETIC_PACKAGE": str(tmp_path / "operator-app")})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "exact_candidate_metadata_operator_package_restored"


def test_different_package_initializer_refuses_before_secret_access(tmp_path):
    result = fresh("""
import os,sys
from pathlib import Path
from types import SimpleNamespace
import schema25_migration_metadata as metadata
import rolling_release
initializer = Path(os.environ['SYNTHETIC_INITIALIZER'])
initializer.write_text('unexpected_configuration = True')
sys.modules['app'] = SimpleNamespace(__file__=str(initializer), __path__=[str(initializer.parent)])
def forbidden(*args):
    raise AssertionError('secret read despite different package source')
try:
    with metadata.initialized_metadata(Path.cwd().parent, SimpleNamespace(_module=forbidden), 'synthetic-runtime:2'):
        raise AssertionError('different package adopted')
except rolling_release.RollingDenied as error:
    assert str(error) == 'schema25_metadata_source_changed'
print('different_package_refused')
""", extra={"SYNTHETIC_INITIALIZER": str(tmp_path / "__init__.py")})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "different_package_refused"
