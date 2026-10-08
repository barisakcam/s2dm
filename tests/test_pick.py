"""Tests for the @pick selection query directive."""

from pathlib import Path

import pytest
from graphql import GraphQLEnumType, GraphQLSchema, print_ast, validate
from graphql import print_schema as print_graphql_schema
from graphql.error import GraphQLSyntaxError

from s2dm.exporters.utils.pick import (
    ALL,
    ARGUMENT_FOR_KIND,
    PickedDefinitions,
    extract_and_validate_picks,
    parse_selection_query,
    picked_type_names,
)
from s2dm.exporters.utils.schema_loader import (
    load_schema,
    print_schema_with_directives_preserved,
    prune_schema_using_query_selection,
)

SCHEMA_PATH = Path("tests/data/pick_schema.graphql")
SEATS = "vehicle { cabin { seats { isOccupied } } }"


def prune(query: str) -> GraphQLSchema:
    schema = load_schema([SCHEMA_PATH])
    return prune_schema_using_query_selection(schema, parse_selection_query(query))


def directive_names(schema: GraphQLSchema) -> set[str]:
    return {directive.name for directive in schema.directives}


class TestWithoutTheDirective:
    def test_unreferenced_definitions_are_dropped(self) -> None:
        schema = prune(f"query Selection {{ {SEATS} }}")

        assert "DateTime" not in schema.type_map
        assert "SeatMaterial" not in schema.type_map
        assert "confidential" not in directive_names(schema)

    def test_a_directive_on_a_pruned_field_is_still_kept(self) -> None:
        """Existing behavior: directives are collected from a type before its fields are deleted."""
        schema = prune(f"query Selection {{ {SEATS} }}")

        assert "range" in directive_names(schema)


class TestPicking:
    def test_unreferenced_scalar_is_kept(self) -> None:
        schema = prune(f'query Selection @pick(scalars: ["DateTime"]) {{ {SEATS} }}')

        assert "DateTime" in schema.type_map

    def test_unapplied_directive_is_kept(self) -> None:
        schema = prune(f'query Selection @pick(directives: ["confidential"]) {{ {SEATS} }}')

        assert "confidential" in directive_names(schema)

    def test_unreferenced_enum_is_kept_whole(self) -> None:
        schema = prune(f'query Selection @pick(enums: ["SeatMaterial"]) {{ {SEATS} }}')

        enum_type = schema.type_map["SeatMaterial"]
        assert isinstance(enum_type, GraphQLEnumType)
        assert list(enum_type.values) == ["CLOTH", "LEATHER", "VINYL"]

    def test_empty_list_keeps_every_definition_of_that_kind(self) -> None:
        schema = prune(f"query Selection @pick(scalars: [], enums: [], directives: []) {{ {SEATS} }}")

        assert "DateTime" in schema.type_map
        assert "SeatMaterial" in schema.type_map
        assert "VelocityUnit" in schema.type_map
        assert "confidential" in directive_names(schema)

    def test_absent_arguments_leave_the_existing_behavior(self) -> None:
        picked = prune(f'query Selection @pick(scalars: ["DateTime"]) {{ {SEATS} }}')
        plain = prune(f"query Selection {{ {SEATS} }}")

        assert "SeatMaterial" not in picked.type_map
        assert set(plain.type_map) < set(picked.type_map)

    def test_the_directive_does_not_reach_the_filtered_schema(self) -> None:
        schema = prune(f'query Selection @pick(scalars: ["DateTime"]) {{ {SEATS} }}')

        assert "pick" not in print_graphql_schema(schema)


class TestValidation:
    @pytest.mark.parametrize(
        ("selection", "message"),
        [
            ('scalars: ["Timestamp"]', "'Timestamp' is not defined in the model"),
            ('enums: ["Vehicle"]', "'Vehicle' is not an enum"),
            ('scalars: ["SeatMaterial"]', "'SeatMaterial' is not a scalar"),
            ('directives: ["constraint"]', "is not defined in the model"),
            ('enums: ["range"]', "is not an enum; list it under 'directives'"),
            ('scalars: ["confidential"]', "is not a scalar; list it under 'directives'"),
            ('directives: ["SeatMaterial"]', "is not a directive; list it under 'enums'"),
            ('enums: ["DateTime"]', "is not an enum; list it under 'scalars'"),
            ('enums: "SeatMaterial"', "must be a list of names"),
            ('unknown: ["x"]', "unknown argument"),
        ],
    )
    def test_invalid_selections_are_reported(self, selection: str, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            prune(f"query Selection @pick({selection}) {{ {SEATS} }}")


class TestEmptySelectionSet:
    def test_a_query_that_only_picks_definitions_needs_no_fields(self) -> None:
        schema = prune('query Selection @pick(enums: ["SeatMaterial"], scalars: ["DateTime"]) {}')

        assert "SeatMaterial" in schema.type_map
        assert "DateTime" in schema.type_map
        assert "Vehicle" not in schema.type_map

    def test_it_matches_selecting_typename(self) -> None:
        braces = prune('query Selection @pick(scalars: ["DateTime"]) {}')
        typename = prune('query Selection @pick(scalars: ["DateTime"]) { __typename }')

        assert print_graphql_schema(braces) == print_graphql_schema(typename)

    def test_a_broken_query_still_reports_its_own_error(self) -> None:
        with pytest.raises(GraphQLSyntaxError, match="Expected ':'"):
            prune("query Selection @pick(enums: [ { vehicle }")


def extract(query: str) -> tuple[str, PickedDefinitions]:
    """Extract the picks, returning the stripped document as text."""
    schema = load_schema([SCHEMA_PATH])
    document, picked = extract_and_validate_picks(schema, parse_selection_query(query))
    return print_ast(document), picked


class TestExtraction:
    def test_the_query_keeps_its_fields_and_loses_the_directive(self) -> None:
        text, picked = extract(f'query Selection @pick(scalars: ["DateTime"]) {{ {SEATS} }}')

        assert picked.scalars == ["DateTime"]
        assert "@pick" not in text
        assert "isOccupied" in text

    def test_a_document_without_a_query_picks_nothing(self) -> None:
        text, picked = extract("fragment SeatFields on Seat { isOccupied }")

        assert picked == PickedDefinitions()
        assert "SeatFields" in text

    def test_the_directive_cannot_be_applied_twice(self) -> None:
        query = f'query Selection @pick(scalars: ["DateTime"]) @pick(enums: []) {{ {SEATS} }}'

        with pytest.raises(ValueError, match="applied more than once"):
            extract(query)

    def test_a_later_query_keeps_the_directive_for_validation_to_reject(self) -> None:
        query = f"query First {{ {SEATS} }}\nquery Second @pick(enums: []) {{ {SEATS} }}"
        schema = load_schema([SCHEMA_PATH])

        document, picked = extract_and_validate_picks(schema, parse_selection_query(query))

        assert picked == PickedDefinitions()
        errors = validate(schema, document)
        assert any("pick" in error.message for error in errors)


class TestIntrospectionNames:
    def test_a_reserved_name_is_refused(self) -> None:
        with pytest.raises(ValueError, match="reserves for introspection"):
            prune(f'query Selection @pick(enums: ["__TypeKind"]) {{ {SEATS} }}')

    def test_keeping_every_definition_leaves_the_reserved_ones_out(self) -> None:
        schema = load_schema([SCHEMA_PATH])

        names = picked_type_names(schema, PickedDefinitions(enums=ALL, scalars=ALL))

        assert "SeatMaterial" in names
        assert not [name for name in names if name.startswith("__")]


class TestPrintedOutput:
    def test_a_query_root_left_without_fields_is_not_printed(self) -> None:
        schema = prune('query Selection @pick(enums: ["SeatMaterial"]) {}')

        printed = print_schema_with_directives_preserved(schema)

        assert "enum SeatMaterial" in printed
        assert "type Query" not in printed


class TestEveryPickableKind:
    """Guards the pairing that validation and collection each spell out separately."""

    @pytest.mark.parametrize("argument_name", [name for _kind, name in ARGUMENT_FOR_KIND])
    def test_keeping_every_definition_of_a_kind_keeps_something(self, argument_name: str) -> None:
        schema = load_schema([SCHEMA_PATH])

        names = picked_type_names(schema, PickedDefinitions(**{argument_name: ALL}))

        assert names, f"'{argument_name}: []' kept nothing; picked_type_names may not handle it"
