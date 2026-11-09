"""Keep OpenAPI's additionalProperties policy consistent with Pydantic request validation."""

from typing import TYPE_CHECKING

from litestar.openapi.spec import Schema
from litestar.plugins.pydantic import PydanticSchemaPlugin

if TYPE_CHECKING:
    from litestar._openapi.schema_generation.schema import SchemaCreator
    from litestar.typing import FieldDefinition


class StrictModelSchemaPlugin(PydanticSchemaPlugin):
    """Litestar 2.25 validates extra='forbid' but omits it from its generated model schema."""

    @classmethod
    def for_pydantic_model(
        cls, field_definition: "FieldDefinition", schema_creator: "SchemaCreator"
    ) -> Schema:
        schema = super().for_pydantic_model(field_definition, schema_creator)
        if getattr(field_definition.annotation, "model_config", {}).get("extra") == "forbid":
            schema.additional_properties = False
        return schema
