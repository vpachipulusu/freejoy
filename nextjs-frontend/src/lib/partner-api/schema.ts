import partnerOpenApi from '../../../public/partner-openapi.json';

/**
 * The partner OpenAPI document, read at build time from the committed copy in `public/` (the same bytes as
 * `docs/partner/openapi.json`; a test and `commands.check_partner_openapi_schema` keep them identical). The page
 * never fetches it at run time and the browser never calls the partner API: the document is the one source of
 * truth for which endpoints exist, and `content.ts` holds the prose around them.
 */

/** Where the document is offered for download, from the frontend's static files. */
export const PARTNER_OPENAPI_PUBLIC_PATH = '/partner-openapi.json';

const METHODS = ['get', 'post', 'put', 'patch', 'delete', 'head', 'options'] as const;

interface SchemaNode {
  type?: string;
  format?: string;
  description?: string;
  examples?: unknown[];
  $ref?: string;
  properties?: Record<string, SchemaNode>;
  required?: string[];
  items?: SchemaNode;
  const?: unknown;
}

interface ParameterObject {
  name: string;
  in: string;
  required?: boolean;
  description?: string;
  schema?: SchemaNode;
}

interface OperationObject {
  operationId: string;
  summary?: string;
  description?: string;
  parameters?: ParameterObject[];
  responses: Record<string, { description?: string }>;
}

export interface OpenApiDocument {
  info: { title: string; version: string };
  paths: Record<string, Partial<Record<(typeof METHODS)[number], OperationObject>>>;
  components?: { schemas?: Record<string, SchemaNode> };
}

export interface ParameterView {
  name: string;
  location: string;
  required: boolean;
  type: string;
  description: string;
}

export interface ResponseView {
  status: string;
  description: string;
}

export interface OperationView {
  operationId: string;
  method: string;
  path: string;
  summary: string;
  description: string;
  parameters: ParameterView[];
  responses: ResponseView[];
}

/** The committed document, typed for what this page reads. */
export const PARTNER_OPENAPI = partnerOpenApi as unknown as OpenApiDocument;

function parameterView(parameter: ParameterObject): ParameterView {
  const schema = parameter.schema ?? {};
  const type = schema.format
    ? `${schema.type ?? 'string'} (${schema.format})`
    : (schema.type ?? '');
  return {
    name: parameter.name,
    location: parameter.in,
    required: parameter.required === true,
    type,
    description: parameter.description ?? schema.description ?? '',
  };
}

/** Every operation of the document, in the order the document lists them. */
export function listOperations(document: OpenApiDocument = PARTNER_OPENAPI): OperationView[] {
  return Object.entries(document.paths).flatMap(([path, item]) =>
    METHODS.flatMap((method) => {
      const operation = item[method];
      if (!operation) return [];
      return [
        {
          operationId: operation.operationId,
          method: method.toUpperCase(),
          path,
          summary: operation.summary ?? '',
          description: operation.description ?? '',
          parameters: (operation.parameters ?? []).map(parameterView),
          responses: Object.entries(operation.responses).map(([status, response]) => ({
            status,
            description: response.description ?? '',
          })),
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
