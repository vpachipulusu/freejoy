/**
 * A small structural check of an example body against an OpenAPI schema node: every property the example has is
 * in the schema, every required property is present, and each value has the declared type, constant, enum member,
 * `anyOf` branch and (for dates) format. It follows `$ref` into `components.schemas`. It is not a full JSON Schema
 * validator (no new dependency), which is enough to catch an example that drifts from the document: a renamed,
 * removed or added field, a wrong type, a value outside an enum or a different constant.
 */

export interface ShapeNode {
  type?: string;
  format?: string;
  $ref?: string;
  properties?: Record<string, ShapeNode>;
  required?: string[];
  items?: ShapeNode;
  anyOf?: ShapeNode[];
  enum?: unknown[];
  const?: unknown;
}

export interface ShapeDocument {
  components?: { schemas?: Record<string, ShapeNode> };
}

const FORMATS: Record<string, RegExp> = {
  date: /^\d{4}-\d{2}-\d{2}$/,
  'date-time': /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/,
};

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

function scalarProblems(value: unknown, schema: ShapeNode, path: string): string[] {
  if (!typeMatches(schema.type, value)) {
    return [`${path} is ${typeOf(value)}, the schema says ${schema.type}`];
  }
  if ('const' in schema && value !== schema.const) {
    return [
      `${path} is ${JSON.stringify(value)}, the schema says it is always ${JSON.stringify(schema.const)}`,
    ];
  }
  if (schema.enum && !schema.enum.includes(value)) {
    return [`${path} is ${JSON.stringify(value)}, not one of ${JSON.stringify(schema.enum)}`];
  }
  const format = schema.format ? FORMATS[schema.format] : undefined;
  if (format && typeof value === 'string' && !format.test(value)) {
    return [`${path} is not written as a ${schema.format}`];
  }
  return [];
}

/** The problems found, as readable strings; an empty list means the example fits the schema. */
export function shapeProblems(
  value: unknown,
  node: ShapeNode,
  document: ShapeDocument,
  path = '$'
): string[] {
  const schema = resolve(node, document);
  if (schema.anyOf) {
    const attempts = schema.anyOf.map((branch) => shapeProblems(value, branch, document, path));
    return attempts.some((problems) => problems.length === 0)
      ? []
      : [`${path} fits none of the ${attempts.length} alternatives the schema allows`];
  }
  const scalar = scalarProblems(value, schema, path);
  if (scalar.length > 0) return scalar;
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
