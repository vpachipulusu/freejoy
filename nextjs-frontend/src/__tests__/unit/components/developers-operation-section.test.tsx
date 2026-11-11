import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';

vi.mock('@/app/globals.css', () => ({}));

import { OperationSection } from '@/components/developers/operation-section';
import { RichText } from '@/components/developers/rich-text';
import type { OperationContent } from '@/lib/partner-api/content';
import { listOperations, type OpenApiDocument } from '@/lib/partner-api/schema';

/** An operation like the offer endpoints FREEJ-58 adds: a PUT with a path parameter, an anyOf one and a body. */
const DOCUMENT: OpenApiDocument = {
  info: { title: 'T', version: '1' },
  components: {
    parameters: {
      Dry: {
        name: 'dry_run',
        in: 'query',
        schema: { type: 'boolean' },
        description: 'Check only.',
      },
    },
    schemas: {
      OfferDraft: {
        type: 'object',
        required: ['title'],
        properties: {
          title: { type: 'string', description: 'The title of the offer.' },
          ends: { anyOf: [{ type: 'string', format: 'date' }, { type: 'null' }] },
        },
      },
    },
  },
  paths: {
    '/partner/v1/offers/{offer_ref}': {
      parameters: [
        {
          name: 'offer_ref',
          in: 'path',
          required: true,
          schema: { type: 'string' },
          description: 'Your reference.',
        },
        { $ref: '#/components/parameters/Dry' },
      ],
      put: {
        operationId: 'partner-offer-put',
        summary: 'Send an offer?',
        requestBody: {
          required: true,
          content: { 'application/json': { schema: { $ref: '#/components/schemas/OfferDraft' } } },
        },
        responses: { '201': {}, '404': {}, '409': {} },
      },
    },
    '/partner/v1/ping': {
      post: {
        operationId: 'partner-ping',
        summary: 'Ping',
        requestBody: {
          content: { 'application/json': { schema: { $ref: '#/components/schemas/OfferDraft' } } },
        },
        responses: { '201': {} },
      },
    },
  },
};

const CONTENT: OperationContent = {
  intro: 'Sends one offer as a draft for an editor to check.',
  notes: [],
  request: 'curl --request PUT \\n  "$FREEJOY_PARTNER_URL/offers/ref-1"',
  requestBody: '{\n  "title": "Example"\n}',
  response: '{}',
  statuses: {
    '404': { meaning: 'No offer of yours has that reference.' },
    '409': { message: 'Conflict', meaning: 'The offer was changed meanwhile.' },
  },
};

const [put, ping] = listOperations(DOCUMENT);

describe('an operation section for a write endpoint', () => {
  it('shows the request body as a table, and never says it takes no parameters', () => {
    render(<OperationSection operation={put} content={CONTENT} />);

    const body = screen.getByRole('table', {
      name: 'Request body of PUT /partner/v1/offers/{offer_ref}',
    });
    const rows = within(body).getAllByRole('row').slice(1);
    expect(
      rows.map((row) =>
        within(row)
          .getAllByRole('cell')
          .map((cell) => cell.textContent)
      )
    ).toEqual([
      ['Yes', 'string', 'The title of the offer.'],
      ['No', 'string (date) or null', ''],
    ]);
    expect(screen.queryByText(/takes no parameters/)).toBeNull();
    expect(screen.getByRole('heading', { level: 4, name: 'Request body' })).toBeInTheDocument();
  });

  it('shows path level and $ref parameters with their types', () => {
    render(<OperationSection operation={put} content={CONTENT} />);

    const table = screen.getByRole('table', {
      name: 'Parameters of PUT /partner/v1/offers/{offer_ref}',
    });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows.map((row) => within(row).getAllByRole('rowheader')[0].textContent)).toEqual([
      'offer_ref',
      'dry_run',
    ]);
    expect(
      within(rows[1])
        .getAllByRole('cell')
        .map((cell) => cell.textContent)
    ).toEqual(['query', 'No', 'boolean', 'Check only.']);
  });

  it('shows an example request body and the statuses of the operation, not the general ones', () => {
    render(<OperationSection operation={put} content={CONTENT} />);

    expect(
      screen.getByRole('region', {
        name: 'Example request body for PUT /partner/v1/offers/{offer_ref}',
      })
    ).toHaveTextContent('"title": "Example"');
    const answers = screen.getByRole('table', {
      name: 'Answers of PUT /partner/v1/offers/{offer_ref}',
    });
    expect(answers).toHaveTextContent('No offer of yours has that reference.');
    expect(answers).not.toHaveTextContent('No endpoint has that path.');
    expect(answers).toHaveTextContent('Conflict');
    expect(answers).toHaveTextContent('The offer was changed meanwhile.');
  });

  it('makes a summary that ends in a question mark one sentence, and leaves out an empty notes list', () => {
    const { container } = render(<OperationSection operation={put} content={CONTENT} />);

    expect(container.querySelector('strong')).toHaveTextContent('Send an offer.');
    expect(container.querySelectorAll('ul')).toHaveLength(0);
  });

  it('says an operation with only a body has a body, not that it has no parameters', () => {
    render(
      <OperationSection
        operation={ping}
        content={{ ...CONTENT, statuses: undefined, request: 'curl x \\n  y' }}
      />
    );

    expect(screen.queryByText(/takes no parameters/)).toBeNull();
    expect(
      screen.getByRole('heading', { level: 4, name: 'Request body (optional)' })
    ).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Parameters' })).toBeNull();
  });

  it('refuses a body with no example, so a build cannot publish it undocumented', () => {
    expect(() =>
      render(<OperationSection operation={put} content={{ ...CONTENT, requestBody: undefined }} />)
    ).toThrow(/has a request body but partner-offer-put has no requestBody example/);
  });
});

describe('rich text', () => {
  it('shows text between backticks as code and everything else as plain text', () => {
    const { container } = render(<RichText text="Set `from` and `to`. Plain." />);

    expect([...container.querySelectorAll('code')].map((code) => code.textContent)).toEqual([
      'from',
      'to',
    ]);
    expect(container.textContent).toBe('Set from and to. Plain.');
  });
});
