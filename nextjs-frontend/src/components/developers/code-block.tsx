interface CodeBlockProps {
  /** Names the example for people using a screen reader, e.g. "Request example". */
  label: string;
  children: string;
}

/**
 * A code example. The `<pre>` scrolls sideways inside its own region (so the page never scrolls sideways at
 * 320px) and takes keyboard focus, so a keyboard user can scroll it; the global focus outline shows where focus is.
 */
export function CodeBlock({ label, children }: CodeBlockProps) {
  return (
    <pre
      role="region"
      aria-label={label}
      tabIndex={0}
      className="mt-3 max-w-full overflow-x-auto rounded-card border border-border bg-secondary p-4 text-sm leading-relaxed text-foreground"
    >
      <code className="font-mono">{children}</code>
    </pre>
  );
}
