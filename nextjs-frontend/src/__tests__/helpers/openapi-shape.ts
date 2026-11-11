/**
 * A small structural check of an example body against an OpenAPI schema node: every property the example has is
 * in the schema, every required property is present, and each value has the declared basic type. It follows
 * `$ref` into `components.schemas`. It is not a full JSON Schema validator (no new dependency), which is enough to
 * catch an example that drifts from the document: a renamed, removed or added field.
 */

export interface ShapeNode {
  type?: string;
  $ref?: string;
  properties?: Record<string, ShapeNode>;
  required?: string[];
  items?: ShapeNode;
}

export interface ShapeDocument {
  components?: { schemas?: Record<string, ShapeNode> };
}

function resolve(node: ShapeNode, document: ShapeDocument): ShapeNode {
  if (!node.$ref) return node;
  const name = node.$ref.replace('#/components/schemas/', '');
  const found = document.components?.schemas?.[name];
  if (!found) throw new Error(`Unknown schema reference ${node.$ref}`);
  return resolve(found, document);
}

function typeOf(value: unknown): string {
  if (value === null) return 'null';
  if (Array.isArray(value)) return 'array';
  if (Number.isInteger(value)) return 'integer';
  return typeof value;
}

function typeMatches(expected: string | undefined, value: unknown): boolean {
  const actual = typeOf(value);
  if (expected === undefined) return true;
  if (expected === 'number') return actual === 'number' || actual === 'integer';
  return expected === actual;
}

/** The problems found, as readable strings; an empty list means the example fits the schema. */
export function shapeProblems(
  value: unknown,
  node: ShapeNode,
  document: ShapeDocument,
  path = '$'
): string[] {
  const schema = resolve(node, document);
  if (!typeMatches(schema.type, value)) {
    return [`${path} is ${typeOf(value)}, the schema says ${schema.type}`];
  }
  if (Array.isArray(value)) {
    return schema.items
      ? value.flatMap((item, index) =>
          shapeProblems(item, schema.items as ShapeNode, document, `${path}[${index}]`)
        )
      : [];
  }
  if (typeof value !== 'object' || value === null || !schema.properties) return [];
  const record = value as Record<string, unknown>;
  const properties = schema.properties;
  const unknown = Object.keys(record)
    .filter((key) => !(key in properties))
    .map((key) => `${path}.${key} is not in the schema`);
  const missing = (schema.required ?? [])
    .filter((key) => !(key in record))
    .map((key) => `${path}.${key} is required by the schema and missing`);
  const nested = Object.entries(record).flatMap(([key, child]) =>
    key in properties ? shapeProblems(child, properties[key], document, `${path}.${key}`) : []
  );
  return [...unknown, ...missing, ...nested];
}
