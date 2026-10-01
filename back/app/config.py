from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL, make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Either a full DATABASE_URL (docker compose, tests) or separate DB_* parts (AWS Lambda).
    database_url: str | None = None
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "meetings"
    db_password: str = "meetings"
    db_name: str = "meetings"
    # Sign in with a short-lived IAM token instead of DB_PASSWORD, over TLS. Aurora clusters created with
    # express configuration (the only kind the AWS Free plan allows) accept nothing else.
    db_iam_auth: bool = False
    aws_region: str = "us-east-1"
    # Open a connection per request instead of pooling (Lambda + Aurora Serverless: idle pooled
    # connections in warm execution environments would keep the database from pausing).
    db_null_pool: bool = False

    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    seed: bool = False

    # Cognito user pool whose ID tokens the API accepts (`make aws-cognito-deploy` fills these in).
    # Empty pool ID = auth disabled: every request acts as one local user (local development, tests).
    cognito_region: str = "us-east-1"
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""
    # The pool's JWKS as JSON. Set on AWS, where the Lambda has no internet route to fetch it;
    # when empty it is downloaded from the issuer on first use.
    cognito_jwks: str = ""

    @property
    def sqlalchemy_url(self) -> URL:
        if self.database_url:
            return make_url(self.database_url)
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )

    @property
    def auth_enabled(self) -> bool:
        return bool(self.cognito_user_pool_id)

    @property
    def cognito_issuer(self) -> str:
        return f"https://cognito-idp.{self.cognito_region}.amazonaws.com/{self.cognito_user_pool_id}"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
