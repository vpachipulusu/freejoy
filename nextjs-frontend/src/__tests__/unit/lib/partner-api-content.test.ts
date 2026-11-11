import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { KEY_LOOKING } from '@/lib/admin-catalogue/partner-key-schema';
import {
  ERROR_CODE_ROWS,
  KEY_PLACEHOLDER,
  LIMIT_ROWS,
  OPERATION_CONTENT,
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
    ...Object.keys(content)
      .filter((id) => !ids.has(id))
      .map((id) => `${id} is not an operation of the schema`),
  ];
}

describe('the partner OpenAPI document the page is built from', () => {
  it('has the operations the page documents, all under /partner/v1', () => {
    const operations = listOperations();

    expect(operations.map((operation) => `${operation.method} ${operation.path}`)).toEqual(
      expect.arrayContaining(['GET /partner/v1/me', 'GET /partner/v1/clicks'])
    );
    for (const operation of operations) {
      expect(operation.path.startsWith('/partner/v1/')).toBe(true);
      expect(operation.operationId).toMatch(/^[a-z][a-z0-9-]*$/);
    }
  });

  it('is byte for byte (line endings aside) the document in docs/partner', () => {
    expect(read('nextjs-frontend', 'public', 'partner-openapi.json')).toBe(
      read('docs', 'partner', 'openapi.json')
    );
  });
});

describe('the prose and examples keyed by operation id', () => {
  it('has an entry for every operation in the schema and names none the schema lacks', () => {
    expect(contentProblems(listOperations(), OPERATION_CONTENT)).toEqual([]);
  });

  it('notices an operation with no prose and an entry for an operation that does not exist', () => {
    const operations = listOperations();
    const extra: OperationView = { ...operations[0], operationId: 'partner-new-thing' };

    expect(contentProblems([...operations, extra], OPERATION_CONTENT)).toEqual([
      'partner-new-thing has no prose and example',
    ]);
    expect(
      contentProblems(operations, { ...OPERATION_CONTENT, 'partner-removed': emptyContent() })
    ).toEqual(['partner-removed is not an operation of the schema']);
  });

  it('gives every operation an introduction, a request that calls its path and a JSON answer', () => {
    for (const operation of listOperations()) {
      const content = OPERATION_CONTENT[operation.operationId];
      const relative = operation.path.replace('/partner/v1', '');

      expect(content.intro.length).toBeGreaterThan(20);
      expect(content.request).toContain(`$FREEJOY_PARTNER_URL${relative}`);
      expect(content.request).toContain('Authorization: Bearer $FREEJOY_PARTNER_KEY');
      expect(() => JSON.parse(content.response)).not.toThrow();
    }
  });

  it('shows an answer that fits the 200 schema of its operation', () => {
    const paths = PARTNER_OPENAPI.paths as unknown as Record<
      string,
      Record<string, { operationId: string; responses: Record<string, unknown> }>
    >;
    const document = PARTNER_OPENAPI as unknown as ShapeDocument;
    for (const operation of listOperations()) {
      const response = paths[operation.path][operation.method.toLowerCase()].responses['200'] as {
        content: { 'application/json': { schema: ShapeNode } };
      };
      const schema = response.content['application/json'].schema;
      const body: unknown = JSON.parse(OPERATION_CONTENT[operation.operationId].response);

      expect(shapeProblems(body, schema, document)).toEqual([]);
    }
  });

  it('the shape check itself notices a missing, an extra and a wrongly typed field', () => {
    const document: ShapeDocument = {};
    const node: ShapeNode = {
      type: 'object',
      required: ['name', 'count'],
      properties: { name: { type: 'string' }, count: { type: 'integer' } },
    };

    expect(shapeProblems({ name: 'x', count: 1 }, node, document)).toEqual([]);
    expect(shapeProblems({ count: 'one', extra: true }, node, document)).toEqual([
      '$.extra is not in the schema',
      '$.name is required by the schema and missing',
      '$.count is string, the schema says integer',
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
      .map((c) => c.response)
      .join('\n');

    expect(text).toMatch(/Example Shop/);
    for (const url of text.match(/https?:\/\/[^"\s]+/g) ?? []) {
      expect(new URL(url).hostname.endsWith('.example')).toBe(true);
    }
  });
});

/** An entry that fits the type, for the negative test. */
function emptyContent(): OperationContent {
  return { intro: '', notes: [], request: '', response: '{}' };
}

describe('the status table', () => {
  it('has a row for every status any operation of the schema can answer with', () => {
    const known = new Set(STATUS_ROWS.map((row) => row.status));
    for (const operation of listOperations()) {
      for (const response of operation.responses) {
        expect(known.has(response.status)).toBe(true);
      }
    }
  });

  it('uses the fixed messages of the backend, and its error code', () => {
    const backend = [
      read('fastapi_backend', 'app', 'partner', 'auth.py'),
      read('fastapi_backend', 'app', 'partner', 'surface.py'),
      read('fastapi_backend', 'app', 'core', 'rate_limit_enforcement.py'),
      read('fastapi_backend', 'app', 'core', 'errors.py'),
    ].join('\n');

    for (const row of STATUS_ROWS.filter((r) => r.message && r.status !== '405')) {
      expect(backend).toContain(`"${row.message}"`);
    }
    expect(ERROR_CODE_ROWS.map((row) => row.code)).toEqual(['invalid_range']);
    expect(read('fastapi_backend', 'app', 'partner', 'clicks_routes.py')).toContain(
      'INVALID_RANGE_CODE = "invalid_range"'
    );
  });
});

describe('the limits stated on the page', () => {
  const value = (name: string) => LIMIT_ROWS.find((row) => row.name === name)?.value ?? '';
  const number = (source: string, pattern: RegExp) => Number(pattern.exec(source)?.[1]);

  it('are the ones in the backend sources', () => {
    const limits = read('fastapi_backend', 'app', 'core', 'rate_limit.py');
    const report = read('fastapi_backend', 'app', 'partner', 'clicks_report.py');
    const retention = read('fastapi_backend', 'app', 'core', 'click_retention.py');
    const surface = read('fastapi_backend', 'app', 'partner', 'surface.py');

    const reads = number(
      limits,
      /PARTNER_READ_POLICY = RateLimitPolicy\(\s*"partner_read", limit=(\d+)/
    );
    const days = number(report, /^MAX_RANGE_DAYS = (\d+)/m);
    const offers = number(report, /^MAX_OFFERS = (\d+)/m);
    const kept = number(retention, /^CLICK_RETENTION_DAYS = (\d+)/m);
    const bytes = Number(
      (/^MAX_BODY_BYTES = ([\d_]+)/m.exec(surface)?.[1] ?? '').replace(/_/g, '')
    );

    expect(value('Reads')).toContain(`${reads} requests a minute`);
    expect(value('Clicks date range')).toContain(`At most ${days} days`);
    expect(value('Clicks offers returned')).toContain(`At most ${offers}`);
    expect(value('Click records kept')).toContain(`${kept} days`);
    expect(value('Request body')).toContain('256 KiB');
    expect(bytes).toBe(256 * 1024);
  });

  it('are also the ones the document states for the clicks endpoint', () => {
    const description = JSON.stringify(PARTNER_OPENAPI);

    expect(description).toContain('at most 92 days');
    expect(description).toContain('At most 100 offers');
    expect(description).toContain('deleted after 395 days');
  });
});

describe('the typed document', () => {
  it('lists operations of a document it is given, in order', () => {
    const document: OpenApiDocument = {
      info: { title: 'T', version: '1' },
      paths: {
        '/partner/v1/b': {
          get: { operationId: 'b-get', responses: { '200': {} } },
          post: {
            operationId: 'b-post',
            summary: 'Make b',
            parameters: [{ name: 'q', in: 'query', required: true, schema: { type: 'string' } }],
            responses: { '201': { description: 'Made' } },
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

  it('refuses a document with no definition example', () => {
    expect(() => clickDefinition({ info: { title: 'T', version: '1' }, paths: {} })).toThrow(
      /no example/
    );
  });
});
