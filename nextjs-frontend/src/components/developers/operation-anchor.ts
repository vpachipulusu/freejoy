/** The id of an operation's heading, so the contents list can link to it. No content imports, safe for any component. */
export function operationAnchor(operationId: string): string {
  return `operation-${operationId}`;
}
