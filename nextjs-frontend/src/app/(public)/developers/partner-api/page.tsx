import type { Metadata } from 'next';
import Link from 'next/link';
import { LegalPage } from '@/components/legal/legal-page';
import { operationAnchor } from '@/components/developers/operation-anchor';
import { OperationSection } from '@/components/developers/operation-section';
import { INLINE_CODE, RichText } from '@/components/developers/rich-text';
import { ScrollTable } from '@/components/developers/scroll-table';
import {
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
  PARTNER_OPENAPI_PUBLIC_PATH,
} from '@/lib/partner-api/schema';
import { NOINDEX_ROBOTS, openGraphFor } from '@/lib/seo/metadata';

const TITLE = 'Partner API';
const DESCRIPTION =
  'How a retailer that Freejoy has agreed to work with sends its offers for review, checks their status and reads its own click counts: the key, each endpoint with examples, the answers and limits, and what a click means.';
const PATH = '/developers/partner-api';

/**
 * Static page in the same draft shell as the legal pages (banner, `noindex`, not in the sitemap), like
 * `/partners`. The endpoint list comes from the committed partner OpenAPI document at build time and the prose
 * from `lib/partner-api/content.ts`; a test fails when the two disagree.
 */
export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: openGraphFor({ title: TITLE, description: DESCRIPTION, path: PATH }),
  robots: NOINDEX_ROBOTS,
};

/** A missing entry stops the build: a page that silently left an endpoint out would be the drift this prevents. */
function contentFor(operationId: string): OperationContent {
  const content = OPERATION_CONTENT[operationId];
  if (!content) {
    throw new Error(
      `The partner API has an operation "${operationId}" with no entry in lib/partner-api/content.ts.`
    );
  }
  return content;
}

const CODE = INLINE_CODE;
const ROW_HEADER = 'px-3 py-2 text-start align-top font-mono font-bold [overflow-wrap:anywhere]';

export default function PartnerApiPage() {
  const operations = listOperations();
  const version = PARTNER_OPENAPI.info.version;
  return (
    <LegalPage
      title="Freejoy partner API"
      summary="How a retailer that Freejoy has agreed to work with sends its offers for review, checks their status and reads its own click counts. What it sends is only ever a draft: an editor decides what is published."
      lastReviewed="Not yet reviewed"
    >
      <h2>Before you start</h2>
      <p>
        The partner API (application programming interface) is not open to every retailer. Freejoy
        gives a key to a retailer by hand, after agreeing to work together. The{' '}
        <Link href="/contact">contact page</Link> will show how to reach Freejoy once a contact
        route is published. When it does, use it to ask for a key, and say who you are and who will
        use the key. We cannot say when a request will be answered.
      </p>
      <p>
        Nothing is published through this API. What you send is stored as a draft. Freejoy&apos;s
        editors check and write every listing before it goes live, and no call here changes that. See the{' '}
        <Link href="/partners">page for retailers</Link> for how listings are checked.
      </p>

      <h2>Making a request</h2>
      <p>
        Call the API from your own server, never from a web page or an app on someone&apos;s phone:
        it sends no cross-origin headers, so a browser cannot read its answers, and a key in a page
        or an app is no longer secret.
      </p>
      <p>
        In the examples, <code className={CODE}>$FREEJOY_PARTNER_URL</code> is the address of the
        Freejoy website followed by <code className={CODE}>/partner/v1</code>. For example, if the
        website were <code className={CODE}>https://www.freejoy.example</code> it would be{' '}
        <code className={CODE}>{PARTNER_BASE_EXAMPLE}</code>. So{' '}
        <code className={CODE}>GET /partner/v1/me</code> is called as{' '}
        <code className={CODE}>$FREEJOY_PARTNER_URL/me</code>. We give you the real address with
        your key. <code className={CODE}>$FREEJOY_PARTNER_KEY</code> is your key.
      </p>
      <p>
        Answers are JSON, a common text format for data. A success is{' '}
        <code className={CODE}>{'{"success": true, "data": ...}'}</code> and an error is{' '}
        <code className={CODE}>{'{"success": false, "error": "..."}'}</code>. Every answer is marked{' '}
        <code className={CODE}>Cache-Control: no-store, private</code>, and none sets a cookie.
      </p>
      <p>
        All dates are UTC, which is Coordinated Universal Time: the same as UK time in winter and
        one hour behind it in summer. A UTC day can therefore start at 1am British Summer Time, and
        differ from a day in your own reports.
      </p>

      <h2>Authentication</h2>
      <p>
        Send your key in the <code className={CODE}>Authorization</code> header as{' '}
        <code className={CODE}>Bearer</code> followed by the key. A key looks like{' '}
        <code className={CODE}>{KEY_PLACEHOLDER}</code>. Nothing else is read: not a query
        parameter, not a cookie. If a request also carries cookies or another credential, it is
        refused, so send none.
      </p>
      <ul>
        <li>A key belongs to one merchant and reaches only that merchant&apos;s offers and counts.</li>
        <li>
          Freejoy shows you the key once, when it is made, and keeps only a one-way hash. If you
          lose it, ask the person at Freejoy who gave you the key for a new one. The old one is then
          revoked.
        </li>
        <li>A key works only in the environment it was made for.</li>
        <li>
          Keep the key out of web pages, apps, URLs and repositories. If it may have leaked, tell
          the person at Freejoy who gave you the key straight away, so that it can be revoked.
        </li>
      </ul>

      <h2>Endpoints</h2>
      <p>The list below is built from the API description, so the two always match.</p>
      <ul>
        {operations.map((operation) => (
          <li key={operation.operationId}>
            <a href={`#${operationAnchor(operation.operationId)}`} className="font-mono">
              {operation.method} {operation.path}
            </a>
          </li>
        ))}
      </ul>
      {operations.map((operation) => (
        <OperationSection
          key={operation.operationId}
          operation={operation}
          content={contentFor(operation.operationId)}
        />
      ))}

      <h2>Answers and errors</h2>
      <p>
        An error has one of these messages, except a refused date range, which has its own message
        and the code <code className={CODE}>invalid_range</code> (below). A request that a rule refuses
        keeps the general message and adds its own code. No error repeats anything you sent. What a status means for one endpoint can be more specific: see its own table.
      </p>
      <ScrollTable
        caption="Statuses and messages"
        headings={['Status', 'Message', 'What it means']}
      >
        {STATUS_ROWS.map((row) => (
          <tr key={row.status}>
            <th scope="row" className={ROW_HEADER}>
              {row.status}
            </th>
            <td>{row.message || 'None'}</td>
            <td>
              <RichText text={row.meaning} />
            </td>
          </tr>
        ))}
      </ScrollTable>
      <p>
        Some errors also carry a <code className={CODE}>code</code> that does not change. Match on
        the code, not on the message.
      </p>
      <ScrollTable caption="Error codes" headings={['Code', 'Status', 'What it means']}>
        {ERROR_CODE_ROWS.map((row) => (
          <tr key={row.code}>
            <th scope="row" className={ROW_HEADER}>
              {row.code}
            </th>
            <td>{row.status}</td>
            <td>
              <RichText text={row.meaning} />
            </td>
          </tr>
        ))}
      </ScrollTable>

      <h2>Limits</h2>
      <ScrollTable caption="Limits" headings={['Limit', 'Value']}>
        {LIMIT_ROWS.map((row) => (
          <tr key={row.name}>
            <th scope="row" className="px-3 py-2 text-start align-top font-bold">
              {row.name}
            </th>
            <td>
              <RichText text={row.value} />
            </td>
          </tr>
        ))}
      </ScrollTable>

      <h2>What a click means</h2>
      <p>{clickDefinition()}</p>
      <p>
        Freejoy&apos;s own click counts are the only figure this API gives. Your own orders and
        visits are the measure of what the clicks became. Freejoy does not call clicks sales.
      </p>

      <h2>Download the API description</h2>
      <p>
        The full description is an OpenAPI document (a standard way to describe an API)
        {version ? `, version ${version}` : ''}. There is no live interactive page for it.{' '}
        <a href={PARTNER_OPENAPI_PUBLIC_PATH} download="freejoy-partner-openapi.json">
          Download the OpenAPI document (JSON)
        </a>
        .
      </p>
    </LegalPage>
  );
}
