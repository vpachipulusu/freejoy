import { STATUS_ROWS, type OperationContent } from '@/lib/partner-api/content';
import type { OperationView } from '@/lib/partner-api/schema';
import { CodeBlock } from './code-block';
import { ScrollTable } from './scroll-table';

interface OperationSectionProps {
  operation: OperationView;
  content: OperationContent;
}

/** The id of an operation's heading, so the section can be linked to and named. */
export function operationAnchor(operationId: string): string {
  return `operation-${operationId}`;
}

const ROW_HEADER = 'px-3 py-2 text-start align-top font-mono font-bold';

function ParametersTable({ operation }: { operation: OperationView }) {
  if (operation.parameters.length === 0) {
    return <p>This endpoint takes no parameters.</p>;
  }
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

function StatusTable({ operation }: { operation: OperationView }) {
  return (
    <ScrollTable
      caption={`Answers of ${operation.method} ${operation.path}`}
      headings={['Status', 'Message', 'What it means']}
    >
      {operation.responses.map((response) => {
        const row = STATUS_ROWS.find((candidate) => candidate.status === response.status);
        return (
          <tr key={response.status}>
            <th scope="row" className={ROW_HEADER}>
              {response.status}
            </th>
            <td>{row?.message || 'None'}</td>
            <td>{row?.meaning ?? ''}</td>
          </tr>
        );
      })}
    </ScrollTable>
  );
}

/** One endpoint: what it is for, its parameters, an example request and answer, and the answers it can give. */
export function OperationSection({ operation, content }: OperationSectionProps) {
  const anchor = operationAnchor(operation.operationId);
  const name = `${operation.method} ${operation.path}`;
  return (
    <section aria-labelledby={anchor}>
      <h3 id={anchor} className="font-mono">
        {name}
      </h3>
      <p>
        <strong>{operation.summary}.</strong> {content.intro}
      </p>
      <ul>
        {content.notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
      <h4 className="mt-6 text-base font-bold">Parameters</h4>
      <ParametersTable operation={operation} />
      <h4 className="mt-6 text-base font-bold">Example request</h4>
      <CodeBlock label={`Example request for ${name}`}>{content.request}</CodeBlock>
      <h4 className="mt-6 text-base font-bold">Example answer</h4>
      <CodeBlock label={`Example answer for ${name}`}>{content.response}</CodeBlock>
      <h4 className="mt-6 text-base font-bold">Answers it can give</h4>
      <StatusTable operation={operation} />
    </section>
  );
}
