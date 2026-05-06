import os
from dataclasses import dataclass


@dataclass
class Config:
    data_dir: str = "data"
    batch_size: int = 50000

    # DB Config
    db_name: str = os.getenv("MYSQL_DATABASE", "")
    db_user: str = os.getenv("MYSQL_USER", "")
    db_password: str = os.getenv("MYSQL_PASSWORD", "")
    db_host: str = os.getenv("DB_HOST", "localhost")
    db_port: int = int(os.getenv("DB_PORT", 3306))
    db_driver: str = os.getenv("DB_DRIVER", "")

    debug: bool = True


app_config = Config()
