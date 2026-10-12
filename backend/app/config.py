from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(PROJECT / '.env'), extra='ignore')
    database_url: str
    database_ssl_ca: str = ''
    encryption_key: str
    integration_key: str
    catalog_url: str = 'http://127.0.0.1:8001/catalog'
    billing_url: str = 'http://127.0.0.1:8002/events'
    catalog_database_url: str = ''
    catalog_mode: str = 'http'
    catalog_cache_seconds: int = 60
    cookie_secure: bool = False
    allowed_origins: str = 'http://127.0.0.1:8000,http://localhost:8000,http://127.0.0.1:5173,http://localhost:5173'
    app_env: str = 'development'
    mock_state_dir: str = ''
    pool_size: int = 10
    pool_overflow: int = 10

@lru_cache
def settings():
    s = Settings()
    if len(s.integration_key) < 32:
        raise ValueError('INTEGRATION_KEY 至少需要32个字符，请运行初始化脚本。')
    if s.app_env == 'production' and not s.cookie_secure:
        raise ValueError('生产环境必须开启安全 Cookie 并配置 HTTPS。')
    return s
