from pydantic_settings import BaseSettings
from pydantic import field_validator


class Settings(BaseSettings):
    # Application
    APP_NAME: str = "AutoBI"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True

    # Base de données
    DATABASE_URL: str = "mysql+pymysql://root:root@localhost:3307/autobi"

    # Sécurité JWT
    SECRET_KEY: str = "change-this-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # Tokens
    DEFAULT_TOKEN_BALANCE: int = 5

    # Serveur
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    #OPEN AI
    OPENAI_API_KEY: str = ""

    # CORS
    CORS_ORIGINS: list[str] = []

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors(cls, v):
        if isinstance(v, str):
            if v.startswith("["):
                import json
                return json.loads(v)
            return [i.strip() for i in v.split(",")]
        return v

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()