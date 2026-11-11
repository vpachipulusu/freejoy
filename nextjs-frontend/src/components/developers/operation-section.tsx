import { describeStatus, type OperationContent } from '@/lib/partner-api/content';
import type { OperationView } from '@/lib/partner-api/schema';
import { CodeBlock } from './code-block';
import { operationAnchor } from './operation-anchor';
import { RichText } from './rich-text';
import { ScrollTable } from './scroll-table';

interface OperationSectionProps {
  operation: OperationView;
  content: OperationContent;
}

const ROW_HEADER = 'px-3 py-2 text-start align-top font-mono font-bold [overflow-wrap:anywhere]';

function ParametersTable({ operation }: { operation: OperationView }) {
  return (
    <ScrollTable
      caption={`Parameters of ${operation.method} ${operation.path}`}
      headings={['Name', 'Where', 'Required', 'Type', 'What it is']}
    >
      {operation.parameters.map((parameter) => (
        <tr key={`${parameter.location}:${parameter.name}`}>
          <th scope="row" className={ROW_HEADER}>
            {parameter.name}
          </th>
          <td>{parameter.location}</td>
          <td>{parameter.required ? 'Yes' : 'No'}</td>
          <td>{parameter.type}</td>
          <td>{parameter.description}</td>
        </tr>
      ))}
    </ScrollTable>
  );
}

function BodyTable({ operation }: { operation: OperationView }) {
  const body = operation.body;
  if (!body) return null;
  return (
    <ScrollTable
      caption={`Request body of ${operation.method} ${operation.path}`}
      headings={['Field', 'Required', 'Type', 'What it is']}
    >
      {body.fields.map((field) => (
        <tr key={field.name}>
          <th scope="row" className={ROW_HEADER}>
            {field.name}
          </th>
          <td>{field.required ? 'Yes' : 'No'}</td>
          <td>{field.type}</td>
          <td>{field.description}</td>
        </tr>
      ))}
    </ScrollTable>
  );
}

function StatusTable({ operation, content }: OperationSectionProps) {
  return (
    <ScrollTable
      caption={`Answers of ${operation.method} ${operation.path}`}
      headings={['Status', 'Message', 'What it means']}
    >
      {operation.responses.map(({ status }) => {
        const text = describeStatus(operation.operationId, status, content);
        return (
          <tr key={status}>
            <th scope="row" className={ROW_HEADER}>
              {status}
            </th>
            <td>{text.message || 'None'}</td>
            <td>
              <RichText text={text.meaning} />
            </td>
          </tr>
        );
      })}
    </ScrollTable>
  );
}

/** The summary as a sentence: one closing full stop, whatever punctuation the document used. */
function sentence(summary: string): string {
  return `${summary.replace(/[.!?\s]+$/, '')}.`;
}

/** One endpoint: what it is for, its parameters or body, an example request and answer, and the answers it can give. */
export function OperationSection({ operation, content }: OperationSectionProps) {
  const anchor = operationAnchor(operation.operationId);
  const name = `${operation.method} ${operation.path}`;
  const hasParameters = operation.parameters.length > 0;
  if (operation.body && !content.requestBody) {
    throw new Error(
      `${name} has a request body but ${operation.operationId} has no requestBody example.`
    );
  }
  return (
    <section aria-labelledby={anchor}>
      <h3 id={anchor} className="scroll-mt-24 font-mono [overflow-wrap:anywhere]">
        {name}
      </h3>
      <p>
        <strong>{sentence(operation.summary)}</strong> {content.intro}
      </p>
      {content.notes.length > 0 && (
        <ul>
          {content.notes.map((note) => (
            <li key={note}>
              <RichText text={note} />
            </li>
          ))}
        </ul>
      )}
      {hasParameters && (
        <>
          <h4 className="mt-6 text-base font-bold">Parameters</h4>
          <ParametersTable operation={operation} />
        </>
      )}
      {operation.body && (
        <>
          <h4 className="mt-6 text-base font-bold">
            Request body{operation.body.required ? '' : ' (optional)'}
          </h4>
          <BodyTable operation={operation} />
        </>
      )}
      {!hasParameters && !operation.body && (
        <p>This endpoint takes no parameters and no request body.</p>
      )}
      <h4 className="mt-6 text-base font-bold">Example request</h4>
      <CodeBlock label={`Example request for ${name}`}>{content.request}</CodeBlock>
      {content.requestBody && (
        <>
          <h4 className="mt-6 text-base font-bold">Example request body</h4>
          <CodeBlock label={`Example request body for ${name}`}>{content.requestBody}</CodeBlock>
        </>
      )}
      <h4 className="mt-6 text-base font-bold">Example answer</h4>
      <CodeBlock label={`Example answer for ${name}`}>{content.response}</CodeBlock>
      <h4 className="mt-6 text-base font-bold">Answers it can give</h4>
      <StatusTable operation={operation} content={content} />
    </section>
  );
}
