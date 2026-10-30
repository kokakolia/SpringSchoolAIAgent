from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", hide_input_in_errors=True, extra="ignore"
    )

    yandex_api_key: str = ""
    yandex_folder_id: str = ""
    server_host: str = "127.0.0.1"
    server_port: int = Field(default=8000, ge=1, le=65535)
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/taskmaster"
    context_budget_tokens: int = Field(default=16000, ge=1000)
    response_reserve_tokens: int = Field(default=2000, ge=1)
    context_compression_threshold: float = Field(default=0.75, gt=0, le=1)
    max_compression_parts: int = Field(default=32, ge=1, le=128)
    app_username: str = "owner"
    app_password: SecretStr = SecretStr("")
    app_allow_http: bool = False
    model_operation_timeout_seconds: int = Field(default=180, ge=1, le=180)
    auth_attempts_per_minute: int = Field(default=10, ge=1, le=100)
    auth_global_attempts_per_minute: int = Field(default=100, ge=1, le=1000)
    max_request_bytes: int = Field(default=1048576, ge=65536, le=4194304)
    auth_session_seconds: int = Field(default=43200, ge=300, le=604800)

    @model_validator(mode="after")
    def budget_has_room(self):
        if self.response_reserve_tokens >= self.context_budget_tokens:
            raise ValueError("Резерв ответа должен быть меньше бюджета контекста")
        return self

    @field_validator("app_password")
    @classmethod
    def strong_password(cls, value):
        if value.get_secret_value() and len(value.get_secret_value()) < 16:
            raise ValueError("APP_PASSWORD должен содержать не менее 16 символов")
        return value


settings = Settings()
