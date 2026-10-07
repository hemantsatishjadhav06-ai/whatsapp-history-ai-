"""Export reviewed schemas without any private runtime configuration."""
import json
from pathlib import Path

from assistant.config import Settings
from assistant.main import create_app
from assistant.messaging import CanonicalEvent


if __name__ == "__main__":
    app = create_app(Settings(_env_file=None, environment="test", database_url="sqlite:///:memory:"))
    target = Path("packages/contracts")
    target.mkdir(parents=True, exist_ok=True)
    (target / "openapi.json").write_text(json.dumps(app.openapi(), indent=2) + "\n")
    (target / "canonical-event.schema.json").write_text(json.dumps(CanonicalEvent.model_json_schema(), indent=2) + "\n")
    app.state.engine.dispose()
    print("Exported OpenAPI and canonical event JSON Schema.")
