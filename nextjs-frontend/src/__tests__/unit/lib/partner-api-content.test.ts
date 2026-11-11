import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { KEY_LOOKING } from '@/lib/admin-catalogue/partner-key-schema';
import {
  describeStatus,
  ERROR_CODE_ROWS,
  KEY_PLACEHOLDER,
  LIMIT_ROWS,
  OPERATION_CONTENT,
  PARTNER_BASE_EXAMPLE,
  STATUS_ROWS,
  type OperationContent,
} from '@/lib/partner-api/content';
import {
  clickDefinition,
  listOperations,
  PARTNER_OPENAPI,
  type OpenApiDocument,
  type OperationView,
} from '@/lib/partner-api/schema';
import { shapeProblems, type ShapeDocument, type ShapeNode } from '../../helpers/openapi-shape';

const REPO = join(process.cwd(), '..');
const read = (...parts: string[]) =>
  readFileSync(join(REPO, ...parts), 'utf8').replace(/\r\n/g, '\n');
/** The whole key, as the backend defines it: 24 lowercase letters or digits, then 64 hex characters. */
const REAL_KEY = /fj_[a-z0-9]{1,16}_[a-z0-9]{24}_[0-9a-f]{64}/i;
const PREFIX = '/partner/v1';
const READ_METHODS = ['GET', 'HEAD', 'OPTIONS'];

/**
 * The files of this repository the facts on the page come from, read by the tests below. The CI `frontend` path
 * filter must list each one (see the last test), so a backend-only change to any of them runs these tests.
 */
const SOURCES = {
  auth: ['fastapi_backend', 'app', 'partner', 'auth.py'],
  surface: ['fastapi_backend', 'app', 'partner', 'surface.py'],
  report: ['fastapi_backend', 'app', 'partner', 'clicks_report.py'],
  routes: ['fastapi_backend', 'app', 'partner', 'clicks_routes.py'],
  limits: ['fastapi_backend', 'app', 'core', 'rate_limit.py'],
  enforcement: ['fastapi_backend', 'app', 'core', 'rate_limit_enforcement.py'],
  errors: ['fastapi_backend', 'app', 'core', 'errors.py'],
  retention: ['fastapi_backend', 'app', 'core', 'click_retention.py'],
  schema: ['docs', 'partner', 'openapi.json'],
} as const;

/** What the page needs for the operations of a document: the problems, as readable strings. */
function contentProblems(
  operations: readonly OperationView[],
  content: Readonly<Record<string, OperationContent>>
): string[] {
  const ids = new Set(operations.map((operation) => operation.operationId));
  return [
    ...operations
      .filter((operation) => !content[operation.operationId])
      .map((operation) => `${operation.operationId} has no prose and example`),
    ...operations
      .filter(
        (operation) => operation.body && content[operation.operationId]?.requestBody === undefined
      )
      .map((operation) => `${operation.operationId} has a request body and no body example`),
    ...Object.keys(content)
      .filter((id) => !ids.has(id))
      .map((id) => `${id} is not an operation of the schema`),
  ];
}

const emptyContent = (): OperationContent => ({
  intro: '',
  notes: [],
  request: '',
  response: '{}',
});

/** The raw operation of the document for a view, to read its schemas. */
function rawOperation(operation: OperationView) {
  const paths = PARTNER_OPENAPI.paths as unknown as Record<
    string,
    Record<
      string,
      {
        requestBody?: { content: { 'application/json': { schema: ShapeNode } } };
        responses: Record<string, { content?: { 'application/json': { schema: ShapeNode } } }>;
      }
    >
  >;
  return paths[operation.path][operation.method.toLowerCase()];
}

/** The schema of the first 2xx answer with a JSON body, or a sentence saying why there is none. */
function successSchema(operation: OperationView): ShapeNode | string {
  const raw = rawOperation(operation);
  const status = Object.keys(raw.responses)
    .filter((code) => /^2\d\d$/.test(code))
    .sort()[0];
  if (!status) return `${operation.operationId} has no 2xx response`;
  return (
    raw.responses[status].content?.['application/json']?.schema ??
    `${operation.operationId}: ${status} has no JSON body`
  );
}

/** A path with `{templated}` parts as a pattern, for a request example that uses a concrete id. */
function pathPattern(relative: string): RegExp {
  const escaped = relative.replace(/[.*+?^$()|[\]\\]/g, '\\$&').replace(/\{[^}]+\}/g, '[^/"?\\s]+');
  return new RegExp(`\\$FREEJOY_PARTNER_URL${escaped}(?=["?\\s])`);
}

describe('the partner OpenAPI document the page is built from', () => {
  it('has the operations the page documents, all under /partner/v1', () => {
    const operations = listOperations();

    expect(operations.map((operation) => `${operation.method} ${operation.path}`)).toEqual(
      expect.arrayContaining(['GET /partner/v1/me', 'GET /partner/v1/clicks'])
    );
    for (const operation of operations) {
      expect(operation.path.startsWith(`${PREFIX}/`)).toBe(true);
      expect(operation.operationId).toMatch(/^[a-z][a-z0-9-]*$/);
    }
  });

  it('is byte for byte (line endings aside) the document in docs/partner', () => {
    expect(read('nextjs-frontend', 'public', 'partner-openapi.json')).toBe(read(...SOURCES.schema));
  });
});

describe('the prose and examples keyed by operation id', () => {
  it('has an entry for every operation in the schema and names none the schema lacks', () => {
    expect(contentProblems(listOperations(), OPERATION_CONTENT)).toEqual([]);
  });

  it('notices an operation with no prose, a body with no example and an entry for no operation', () => {
    const operations = listOperations();
    const extra: OperationView = { ...operations[0], operationId: 'partner-new-thing' };
    const withBody: OperationView = {
      ...operations[0],
      body: { required: true, fields: [] },
    };

    expect(contentProblems([...operations, extra], OPERATION_CONTENT)).toEqual([
      'partner-new-thing has no prose and example',
    ]);
    expect(contentProblems([withBody, operations[1]], OPERATION_CONTENT)).toEqual([
      `${withBody.operationId} has a request body and no body example`,
    ]);
    expect(
      contentProblems(operations, { ...OPERATION_CONTENT, 'partner-removed': emptyContent() })
    ).toEqual(['partner-removed is not an operation of the schema']);
  });

  it('gives every operation an introduction and a JSON answer', () => {
    for (const operation of listOperations()) {
      const content = OPERATION_CONTENT[operation.operationId];

      expect(content.intro.length).toBeGreaterThan(20);
      expect(() => JSON.parse(content.response)).not.toThrow();
    }
  });

  it('writes every request as real lines: a backslash and a line break continue it, never backslash and n', () => {
    for (const operation of listOperations()) {
      const request = OPERATION_CONTENT[operation.operationId].request;
      const lines = request.split('\n');

      expect(lines.length).toBeGreaterThan(1);
      expect(request).not.toContain('\\n');
      expect(lines[0].startsWith('curl ')).toBe(true);
      for (const line of lines.slice(0, -1)) {
        expect(line.endsWith(' \\')).toBe(true);
        expect(line.endsWith('\\\\')).toBe(false);
      }
      expect(lines[lines.length - 1].endsWith('\\')).toBe(false);
      expect(request).toContain('Authorization: Bearer $FREEJOY_PARTNER_KEY');
    }
  });

  it('calls the path of its operation under the address the page defines', () => {
    const origin = new URL(PARTNER_BASE_EXAMPLE).origin;

    expect(PARTNER_BASE_EXAMPLE).toBe(`${origin}${PREFIX}`);
    expect(new URL(PARTNER_BASE_EXAMPLE).hostname.endsWith('.example')).toBe(true);
    for (const operation of listOperations()) {
      const relative = operation.path.slice(PREFIX.length);

      expect(OPERATION_CONTENT[operation.operationId].request).toMatch(pathPattern(relative));
      // The base address followed by the example's path is the operation's full address.
      expect(`${PARTNER_BASE_EXAMPLE}${relative}`).toBe(`${origin}${operation.path}`);
    }
  });

  it('shows an answer, and a request body, that fit the schemas of their operation', () => {
    const document = PARTNER_OPENAPI as unknown as ShapeDocument;
    for (const operation of listOperations()) {
      const schema = successSchema(operation);
      expect(typeof schema, String(schema)).not.toBe('string');
      const body: unknown = JSON.parse(OPERATION_CONTENT[operation.operationId].response);

      expect(shapeProblems(body, schema as ShapeNode, document)).toEqual([]);
      const example = OPERATION_CONTENT[operation.operationId].requestBody;
      if (operation.body) {
        const requestSchema =
          rawOperation(operation).requestBody?.content['application/json'].schema;
        expect(
          shapeProblems(JSON.parse(example ?? 'null'), requestSchema as ShapeNode, document)
        ).toEqual([]);
      } else {
        expect(example).toBeUndefined();
      }
    }
  });

  it('the shape check notices a missing, an extra and a wrongly typed field', () => {
    const node: ShapeNode = {
      type: 'object',
      required: ['name', 'count'],
      properties: { name: { type: 'string' }, count: { type: 'integer' } },
    };

    expect(shapeProblems({ name: 'x', count: 1 }, node, {})).toEqual([]);
    expect(shapeProblems({ count: 'one', extra: true }, node, {})).toEqual([
      '$.extra is not in the schema',
      '$.name is required by the schema and missing',
      '$.count is string, the schema says integer',
    ]);
  });

  it('the shape check notices a wrong constant, a value outside an enum, a bad date and a failed anyOf', () => {
    const node: ShapeNode = {
      type: 'object',
      properties: {
        success: { type: 'boolean', const: true },
        status: { type: 'string', enum: ['pending', 'accepted'] },
        day: { type: 'string', format: 'date' },
        note: { anyOf: [{ type: 'string' }, { type: 'null' }] },
      },
    };

    expect(
      shapeProblems({ success: true, status: 'pending', day: '2026-03-01', note: null }, node, {})
    ).toEqual([]);
    expect(
      shapeProblems({ success: false, status: 'approved', day: '1 March', note: 5 }, node, {})
    ).toEqual([
      '$.success is false, the schema says it is always true',
      '$.status is "approved", not one of ["pending","accepted"]',
      '$.day is not written as a date',
      '$.note fits none of the 2 alternatives the schema allows',
    ]);
  });

  it('uses the click definition of the document, word for word, in its answer', () => {
    const body = JSON.parse(OPERATION_CONTENT['partner-clicks'].response) as {
      data: { definition: string };
    };

    expect(body.data.definition).toBe(clickDefinition());
    expect(clickDefinition()).toMatch(/not a sale/);
  });

  it('keeps every example free of anything that looks like a key', () => {
    const everything = [
      ...Object.values(OPERATION_CONTENT).flatMap((c) => [
        c.intro,
        c.request,
        c.response,
        c.requestBody ?? '',
        ...c.notes,
      ]),
      KEY_PLACEHOLDER,
      ...STATUS_ROWS.map((row) => row.meaning),
      ...ERROR_CODE_ROWS.map((row) => row.meaning),
      ...LIMIT_ROWS.map((row) => row.value),
    ];

    expect(KEY_PLACEHOLDER).toBe('fj_production_<key id>_<secret>');
    for (const text of everything) {
      expect(text).not.toMatch(KEY_LOOKING);
      expect(text).not.toMatch(REAL_KEY);
    }
  });

  it('the key patterns do catch a real-looking key, so the check above can fail', () => {
    const fake = `fj_production_${'a'.repeat(24)}_${'0'.repeat(64)}`;

    expect(fake).toMatch(KEY_LOOKING);
    expect(fake).toMatch(REAL_KEY);
  });

  it('uses no merchant name or id that looks real: placeholders and .example addresses only', () => {
    const text = Object.values(OPERATION_CONTENT)
      .map((c) => `${c.response}\n${c.request}\n${c.requestBody ?? ''}`)
      .join('\n');

    expect(text).toMatch(/Example Shop/);
    for (const url of text.match(/https?:\/\/[^"\s]+/g) ?? []) {
      expect(new URL(url).hostname.endsWith('.example')).toBe(true);
    }
  });
});

describe('the status table', () => {
  it('has a meaning for every status any operation of the schema can answer with', () => {
    for (const operation of listOperations()) {
      for (const response of operation.responses) {
        expect(() => describeStatus(operation.operationId, response.status)).not.toThrow();
      }
    }
  });

  it('says a status of an operation it does not know is an error, not "None"', () => {
    expect(() => describeStatus('partner-me', '409')).toThrow(
      /Status 409 of partner-me has no row/
    );
    expect(
      describeStatus('partner-me', '409', {
        ...emptyContent(),
        statuses: { '409': { meaning: 'A clash.' } },
      })
    ).toEqual({
      message: '',
      meaning: 'A clash.',
    });
  });

  it('gives each operation the meanings that are true of it', () => {
    expect(describeStatus('partner-clicks', '422').meaning).toMatch(/invalid_range/);
    expect(describeStatus('partner-me', '422').meaning).not.toMatch(/range|from|invalid_range/);
    expect(describeStatus('partner-me', '401').meaning).toBe(
      describeStatus('partner-clicks', '401').meaning
    );
  });

  it('uses the fixed messages of the backend, and its error codes', () => {
    const backend = [
      read(...SOURCES.auth),
      read(...SOURCES.surface),
      read(...SOURCES.enforcement),
      read(...SOURCES.errors),
    ].join('\n');

    // 405 is Starlette's own text, so it is not in these files; every other message is.
    for (const row of STATUS_ROWS.filter((r) => r.message && r.status !== '405')) {
      expect(backend).toContain(`"${row.message}"`);
    }
    const schema = JSON.parse(read(...SOURCES.schema)) as {
      components: {
        schemas: Record<string, { properties: { code: { anyOf: { const?: string }[] } } }>;
      };
    };
    const codes = schema.components.schemas.PartnerRangeErrorResponse.properties.code.anyOf
      .map((branch) => branch.const)
      .filter((code): code is string => typeof code === 'string');
    expect(ERROR_CODE_ROWS.map((row) => row.code).sort()).toEqual(codes.sort());
    expect(read(...SOURCES.routes)).toContain('INVALID_RANGE_CODE = "invalid_range"');
  });
});

describe('the limits stated on the page', () => {
  const value = (name: string) => LIMIT_ROWS.find((row) => row.name === name)?.value ?? '';
  const number = (source: string, pattern: RegExp) => Number(pattern.exec(source)?.[1]);
  const policy = (name: string, source: string) =>
    number(source, new RegExp(`${name} = RateLimitPolicy\\(\\s*"[a-z_]+", limit=(\\d+)`));

  it('are the ones in the backend sources', () => {
    const limits = read(...SOURCES.limits);
    const report = read(...SOURCES.report);
    const retention = read(...SOURCES.retention);
    const surface = read(...SOURCES.surface);

    const days = number(report, /^MAX_RANGE_DAYS = (\d+)/m);
    const offers = number(report, /^MAX_OFFERS = (\d+)/m);
    const first = number(report, /^MIN_DATE = date\((\d+),/m);
    const last = number(report, /^MAX_DATE = date\((\d+),/m);
    const kept = number(retention, /^CLICK_RETENTION_DAYS = (\d+)/m);
    const bytes = Number(
      (/^MAX_BODY_BYTES = ([\d_]+)/m.exec(surface)?.[1] ?? '').replace(/_/g, '')
    );

    expect(value('Reads')).toContain(`${policy('PARTNER_READ_POLICY', limits)} requests a minute`);
    expect(value('Clicks reads')).toContain(
      `${policy('PARTNER_CLICKS_POLICY', limits)} requests a minute`
    );
    expect(value('Clicks reads')).toContain(`the ${policy('PARTNER_READ_POLICY', limits)} above`);
    expect(value('Clicks date range')).toContain(`At most ${days} days`);
    expect(value('Clicks date range')).toContain(`between the years ${first} and ${last}`);
    expect(ERROR_CODE_ROWS[0].meaning).toContain(
      `covers more than ${days} days (counting both ends)`
    );
    expect(ERROR_CODE_ROWS[0].meaning).toContain(`the years ${first} to ${last}`);
    expect(value('Clicks offers returned')).toContain(`At most ${offers}`);
    expect(value('Click records kept')).toContain(`${kept} days`);
    expect(value('Request body')).toContain(`256 KiB (262,144 bytes)`);
    expect(bytes).toBe(256 * 1024);
    expect(String(bytes.toLocaleString('en-GB'))).toBe('262,144');
  });

  it('are also the ones the document states for the clicks endpoint', () => {
    const description = JSON.stringify(PARTNER_OPENAPI);

    expect(description).toContain('at most 92 days');
    expect(description).toContain('At most 100 offers');
    expect(description).toContain('deleted after 395 days');
  });

  it('say when the key writes, once an operation is not a read', () => {
    const writes = listOperations().some((operation) => !READ_METHODS.includes(operation.method));

    if (writes) {
      expect(LIMIT_ROWS.map((row) => row.name)).toContain('Writes');
    } else {
      expect(LIMIT_ROWS.map((row) => row.name)).not.toContain('Writes');
    }
  });
});

describe('the CI path filter', () => {
  it('lists every file this test reads, so a backend-only change to one of them runs these tests', () => {
    const ci = read('.github', 'workflows', 'ci.yml');
    const section = ci.slice(ci.indexOf('frontend:\n'), ci.indexOf('backend:\n'));
    const patterns = [...section.matchAll(/^\s+- '([^']+)'/gm)].map((match) => match[1]);
    const covers = (file: string) =>
      patterns.some((pattern) =>
        pattern.endsWith('/**') ? file.startsWith(pattern.slice(0, -2)) : file === pattern
      );

    for (const parts of Object.values(SOURCES)) {
      const file = parts.join('/');
      expect(
        covers(file),
        `${file} is not in the frontend filter of .github/workflows/ci.yml`
      ).toBe(true);
    }
  });
});

describe('the typed document', () => {
  const base = { info: { title: 'T', version: '1' } };

  it('lists operations of a document it is given, in order, with their parameters', () => {
    const document: OpenApiDocument = {
      ...base,
      paths: {
        '/partner/v1/b': {
          get: { operationId: 'b-get', summary: 'Read b', responses: { '200': {} } },
          post: {
            operationId: 'b-post',
            summary: 'Make b.',
            parameters: [{ name: 'q', in: 'query', required: true, schema: { type: 'string' } }],
            responses: { '201': {} },
          },
        },
      },
    };

    expect(listOperations(document).map((operation) => operation.operationId)).toEqual([
      'b-get',
      'b-post',
    ]);
    expect(listOperations(document)[1].parameters).toEqual([
      { name: 'q', location: 'query', required: true, type: 'string', description: '' },
    ]);
  });

  it('reads an anyOf type, an enum, a $ref parameter and a parameter of the path item', () => {
    const document: OpenApiDocument = {
      ...base,
      components: {
        parameters: {
          Page: {
            name: 'page',
            in: 'query',
            schema: { type: 'integer' },
            description: 'Which page.',
          },
        },
      },
      paths: {
        '/partner/v1/things/{thing_id}': {
          parameters: [
            { name: 'thing_id', in: 'path', required: true, schema: { type: 'string' } },
            { $ref: '#/components/parameters/Page' },
          ],
          get: {
            operationId: 'thing-get',
            summary: 'Read a thing',
            parameters: [
              {
                name: 'state',
                in: 'query',
                schema: { anyOf: [{ type: 'string', enum: ['open', 'closed'] }, { type: 'null' }] },
              },
            ],
            responses: { '200': {} },
          },
        },
      },
    };

    const [operation] = listOperations(document);

    expect(operation.parameters.map((p) => [p.name, p.location, p.type])).toEqual([
      ['thing_id', 'path', 'string'],
      ['page', 'query', 'integer'],
      ['state', 'query', 'one of open, closed or null'],
    ]);
    expect(operation.parameters[1].description).toBe('Which page.');
  });

  it('reads a JSON request body into fields, following $ref', () => {
    const document: OpenApiDocument = {
      ...base,
      components: {
        schemas: {
          Draft: {
            type: 'object',
            required: ['title'],
            properties: {
              title: { type: 'string', description: 'The title.' },
              ends: { anyOf: [{ type: 'string', format: 'date' }, { type: 'null' }] },
              tags: { type: 'array', items: { type: 'string' } },
            },
          },
        },
      },
      paths: {
        '/partner/v1/drafts': {
          post: {
            operationId: 'draft-create',
            summary: 'Create a draft',
            requestBody: {
              required: true,
              content: { 'application/json': { schema: { $ref: '#/components/schemas/Draft' } } },
            },
            responses: { '201': {} },
          },
        },
      },
    };

    const [operation] = listOperations(document);

    expect(operation.parameters).toEqual([]);
    expect(operation.body).toEqual({
      required: true,
      fields: [
        { name: 'title', required: true, type: 'string', description: 'The title.' },
        { name: 'ends', required: false, type: 'string (date) or null', description: '' },
        { name: 'tags', required: false, type: 'list of string', description: '' },
      ],
    });
  });

  it.each([
    ['an operation with no summary', { operationId: 'x', responses: {} }, /no summary/],
    [
      'a parameter it cannot resolve',
      {
        operationId: 'x',
        summary: 'X',
        parameters: [{ $ref: '#/components/parameters/Gone' }],
        responses: {},
      },
      /cannot show/,
    ],
    [
      'a body that is not JSON',
      {
        operationId: 'x',
        summary: 'X',
        requestBody: { content: { 'text/plain': {} } },
        responses: {},
      },
      /not application\/json/,
    ],
    [
      'a body that is not an object',
      {
        operationId: 'x',
        summary: 'X',
        requestBody: { content: { 'application/json': { schema: { type: 'array' } } } },
        responses: {},
      },
      /not an object/,
    ],
  ])('refuses %s instead of rendering something wrong', (_name, operation, message) => {
    const document = { ...base, paths: { '/partner/v1/x': { get: operation } } } as OpenApiDocument;

    expect(() => listOperations(document)).toThrow(message);
  });

  it('refuses a document with no definition example', () => {
    expect(() => clickDefinition({ info: { title: 'T' }, paths: {} })).toThrow(/no example/);
  });
});
