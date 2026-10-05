import type { DocumentNode } from "graphql";
import { Kind, parse } from "graphql";

// GraphQL rejects an empty selection set, and @pick is defined by s2dm rather than by the
// model, so a selection query the server accepts does not parse or validate here untouched.
const EMPTY_SELECTION_SET = /\{\s*\}\s*$/;
const NOTHING_SELECTED = "{ __typename }";
const PICK_DIRECTIVE = "pick";

/**
 * Parse a selection query, reading an empty selection set as selecting no fields.
 *
 * A query that only picks definitions has no fields to name. Such a query is read as
 * selecting `__typename`, which every type carries and which names nothing in the model.
 */
export function parseSelectionQuery(text: string): DocumentNode {
	try {
		return parse(text);
	} catch (error) {
		const stripped = text.trimEnd();
		const repaired = stripped.replace(EMPTY_SELECTION_SET, NOTHING_SELECTED);
		if (repaired === stripped) {
			throw error;
		}
		return parse(repaired);
	}
}

/**
 * Remove @pick from every operation so the document can be validated against the model,
 * which does not define the directive.
 */
export function withoutPickDirective(document: DocumentNode): DocumentNode {
	return {
		...document,
		definitions: document.definitions.map((definition) => {
			if (
				definition.kind !== Kind.OPERATION_DEFINITION ||
				!definition.directives?.length
			) {
				return definition;
			}
			return {
				...definition,
				directives: definition.directives.filter(
					(directive) => directive.name.value !== PICK_DIRECTIVE,
				),
			};
		}),
	};
}

// The model does not define @pick, so an editor validating against it alone underlines every
// selection query that uses one. Added for validation and completion only; never exported.
const PICK_DIRECTIVE_SDL =
	"directive @pick(enums: [String!], scalars: [String!], directives: [String!]) on QUERY";

/**
 * Return the schema text with the @pick definition, so an editor can validate queries using it.
 *
 * A model that already defines the directive is returned unchanged, since a duplicate
 * definition would make the schema fail to build.
 */
export function withPickDirective(schemaText: string): string {
	if (/^\s*directive\s+@pick\b/m.test(schemaText)) {
		return schemaText;
	}
	return `${schemaText}\n\n${PICK_DIRECTIVE_SDL}\n`;
}
