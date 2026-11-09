from enum import StrEnum
from pydantic import BaseModel, Field


class Env(StrEnum):
    Production = "PRODUCTION"
    Development = "DEVELOPMENT"
    Testing = "TESTING"

class App(BaseModel):
    model_config = SettingsConfigDict(env_file=".env")

    service_id: str
    env: Env
    curr_version: str
    accept_from_version: str = Field(..., description="Used for backward compatability with matches.")


    class Persistence(BaseModel):
        postgres_dsn: str = Field(..., description="PostgreSQL DSN.")