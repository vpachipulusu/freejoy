import 'server-only';
import partnerOpenApi from '../../../public/partner-openapi.json';

/**
 * The partner OpenAPI document, read at build time from the committed copy in `public/` (the same bytes as
 * `docs/partner/openapi.json`; a test and `commands.check_partner_openapi_schema` keep them identical). The page
 * never fetches it at run time and the browser never calls the partner API: the document is the one source of
 * truth for which endpoints exist, and `content.ts` holds the prose around them.
 *
 * `server-only` keeps the document and the prose out of a client bundle: a client component that imported this
 * module would fail the build instead of shipping them to every visitor.
 *
 * The page renders parameters (inline, `$ref` and path level, with `anyOf`, `enum` and `format` types), a JSON
 * request body and the statuses of each operation. Anything it cannot show it refuses with an error, so a build
 * never publishes an endpoint that the page would describe wrongly.
 */

/** Where the document is offered for download, from the frontend's static files. */
export const PARTNER_OPENAPI_PUBLIC_PATH = '/partner-openapi.json';

const METHODS = ['get', 'post', 'put', 'patch', 'delete', 'head', 'options'] as const;
const JSON_CONTENT = 'application/json';

export interface SchemaNode {
  type?: string;
  format?: string;
  description?: string;
  examples?: unknown[];
  $ref?: string;
  properties?: Record<string, SchemaNode>;
  required?: string[];
  items?: SchemaNode;
  anyOf?: SchemaNode[];
  enum?: unknown[];
  const?: unknown;
  title?: string;
}

interface ParameterObject {
  name?: string;
  in?: string;
  required?: boolean;
  description?: string;
  schema?: SchemaNode;
  $ref?: string;
}

interface RequestBodyObject {
  required?: boolean;
  content?: Record<string, { schema?: SchemaNode }>;
}

type ResponsesObject = Record<
  string,
  { description?: string; content?: Record<string, { schema?: SchemaNode }> }
>;

interface OperationObject {
  operationId: string;
  summary?: string;
  description?: string;
  parameters?: ParameterObject[];
  requestBody?: RequestBodyObject;
  responses: ResponsesObject;
}

type PathItem = Partial<Record<(typeof METHODS)[number], OperationObject>> & {
  parameters?: ParameterObject[];
};

export interface OpenApiDocument {
  info: { title: string; version?: string };
  paths: Record<string, PathItem>;
  components?: {
    schemas?: Record<string, SchemaNode>;
    parameters?: Record<string, ParameterObject>;
  };
}

export interface ParameterView {
  name: string;
  location: string;
  required: boolean;
  type: string;
  description: string;
}

export interface BodyFieldView {
  name: string;
  required: boolean;
  type: string;
  description: string;
}

export interface BodyView {
  required: boolean;
  fields: BodyFieldView[];
}

export interface ResponseView {
  status: string;
}

export interface OperationView {
  operationId: string;
  method: string;
  path: string;
  summary: string;
  parameters: ParameterView[];
  body: BodyView | null;
  responses: ResponseView[];
}

/** The committed document, as the page reads it. */
export const PARTNER_OPENAPI: OpenApiDocument = partnerOpenApi;

function refName(ref: string, prefix: string, where: string): string {
  if (!ref.startsWith(prefix)) throw new Error(`${where}: cannot follow the reference ${ref}.`);
  return ref.slice(prefix.length);
}

function resolveSchema(node: SchemaNode, document: OpenApiDocument, where: string): SchemaNode {
  if (!node.$ref) return node;
  const name = refName(node.$ref, '#/components/schemas/', where);
  const found = document.components?.schemas?.[name];
  if (!found) throw new Error(`${where}: the schema ${name} is not in the document.`);
  return resolveSchema(found, document, where);
}

/** A reader's name for a schema's type: `anyOf` is joined ("string or null"), `enum` lists its values. */
export function typeLabel(node: SchemaNode, document: OpenApiDocument, where: string): string {
  if (node.$ref) {
    const resolved = resolveSchema(node, document, where);
    return resolved.type === 'object' || resolved.properties
      ? 'object'
      : typeLabel(resolved, document, where);
  }
  if (node.anyOf) {
    const labels = node.anyOf.map((branch) => typeLabel(branch, document, where));
    return [...new Set(labels)].join(' or ');
  }
  if (node.enum) return `one of ${node.enum.map(String).join(', ')}`;
  if (node.const !== undefined) return `exactly ${String(node.const)}`;
  const base = node.type ?? '';
  if (!base) throw new Error(`${where}: a type the page cannot name.`);
  if (base === 'array' && node.items) return `list of ${typeLabel(node.items, document, where)}`;
  return node.format ? `${base} (${node.format})` : base;
}

function parameterView(
  parameter: ParameterObject,
  document: OpenApiDocument,
  where: string
): ParameterView {
  const resolved = parameter.$ref
    ? document.components?.parameters?.[refName(parameter.$ref, '#/components/parameters/', where)]
    : parameter;
  if (!resolved?.name || !resolved.in || !resolved.schema) {
    throw new Error(`${where}: a parameter the page cannot show (no name, location or schema).`);
  }
  return {
    name: resolved.name,
    location: resolved.in,
    required: resolved.required === true,
    type: typeLabel(resolved.schema, document, `${where} parameter ${resolved.name}`),
    description: resolved.description ?? resolved.schema.description ?? '',
  };
}

/** Path level and operation level parameters; an operation's own parameter replaces a path one of the same name. */
function parameterViews(
  pathItem: PathItem,
  operation: OperationObject,
  document: OpenApiDocument,
  where: string
): ParameterView[] {
  const own = (operation.parameters ?? []).map((p) => parameterView(p, document, where));
  const inherited = (pathItem.parameters ?? [])
    .map((p) => parameterView(p, document, where))
    .filter((p) => !own.some((o) => o.name === p.name && o.location === p.location));
  return [...inherited, ...own];
}

function bodyView(
  body: RequestBodyObject | undefined,
  document: OpenApiDocument,
  where: string
): BodyView | null {
  if (!body) return null;
  const schema = body.content?.[JSON_CONTENT]?.schema;
  if (!schema) throw new Error(`${where}: a request body that is not ${JSON_CONTENT}.`);
  const resolved = resolveSchema(schema, document, where);
  if (!resolved.properties) {
    throw new Error(`${where}: a request body that is not an object with named fields.`);
  }
  const required = new Set(resolved.required ?? []);
  return {
    required: body.required === true,
    fields: Object.entries(resolved.properties).map(([name, field]) => {
      const detail = resolveSchema(field, document, `${where} field ${name}`);
      return {
        name,
        required: required.has(name),
        type: typeLabel(field, document, `${where} field ${name}`),
        description: field.description ?? detail.description ?? '',
      };
    }),
  };
}

/** Every operation of the document, in the order the document lists them. */
export function listOperations(document: OpenApiDocument = PARTNER_OPENAPI): OperationView[] {
  return Object.entries(document.paths).flatMap(([path, item]) =>
    METHODS.flatMap((method) => {
      const operation = item[method];
      if (!operation) return [];
      const where = `${method.toUpperCase()} ${path}`;
      if (!operation.summary) throw new Error(`${where}: an operation with no summary.`);
      return [
        {
          operationId: operation.operationId,
          method: method.toUpperCase(),
          path,
          summary: operation.summary,
          parameters: parameterViews(item, operation, document, where),
          body: bodyView(operation.requestBody, document, where),
          responses: Object.keys(operation.responses).map((status) => ({ status })),
        },
      ];
    })
  );
}

/** The fixed text the clicks response carries as `definition`, taken from the document's own example. */
export function clickDefinition(document: OpenApiDocument = PARTNER_OPENAPI): string {
  const example = document.components?.schemas?.ClicksData?.properties?.definition?.examples?.[0];
  if (typeof example !== 'string') {
    throw new Error('The partner schema has no example for ClicksData.definition.');
  }
  return example;
}
