import structlog


def configure_logging() -> None:
    """Configure structlog once. Called from both the FastAPI app (main.py)
    and the CLI (glossary/cli.py) so the two entry points can't drift apart."""
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
    )
