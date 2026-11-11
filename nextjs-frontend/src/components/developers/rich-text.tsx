/** The class of an inline code name: wraps inside its box so a long name cannot widen the page at 320px. */
export const INLINE_CODE = 'font-mono text-sm [overflow-wrap:anywhere]';

/** Plain text in which `names between backticks` are shown as code. No other markup is read. */
export function RichText({ text }: { text: string }) {
  return (
    <>
      {text.split(/`([^`]+)`/).map((part, index) =>
        index % 2 === 1 ? (
          <code key={index} className={INLINE_CODE}>
            {part}
          </code>
        ) : (
          part
        )
      )}
    </>
  );
}
