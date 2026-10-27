import pytest

from agent.config import Settings


@pytest.fixture
def make_settings(tmp_path):
    def factory(**kw) -> Settings:
        base = dict(
            agent_api_key="clave-de-prueba",
            agent_db_path=tmp_path / "agent.db",
            workspace_dir=tmp_path / "ws",
            enable_web=True,
            enable_files=True,
            enable_code=False,
            enable_memory=False,
            enable_screen=False,
            taint_lock=True,
        )
        base.update(kw)
        return Settings(_env_file=None, **base)

    return factory
