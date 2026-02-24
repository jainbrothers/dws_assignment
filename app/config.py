from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = (
        "postgresql+asyncpg://trade_user:trade_pass@localhost:5432/trade_store"
    )
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_security_protocol: str = "PLAINTEXT"  # PLAINTEXT for local, SSL for MSK
    kafka_topic_replication_factor: int = 1  # 1 for local, set to broker count in AWS
    kafka_topic_trades_inbound: str = "trades-inbound"
    environment: str = "development"
    log_level: str = "INFO"

    # DynamoDB
    aws_region: str = "ap-south-1"
    dynamodb_table_name: str = "trade-requests"
    dynamodb_endpoint_url: Optional[str] = (
        None  # Override for DynamoDB Local: http://localhost:8001
    )


settings = Settings()
