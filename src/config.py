from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    yandex_api_key: str = ""
    yandex_folder_id: str = ""
    matrix_path: str = "matrix.xlsx"
    server_host: str = "127.0.0.1"
    server_port: int = 8000
    database_url: str = "postgresql://postgres:postgres@localhost:5432/taskmaster"


settings = Settings()
