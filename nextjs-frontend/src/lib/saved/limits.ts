/**
 * How many saved slugs one request to the server may carry. The Saved page sends the browser's list in
 * chunks of this size; the server action refuses anything bigger. The browser-side cap on the whole
 * list is `MAX_SAVED_OFFERS` in `saved-store.ts`.
 */
export const SAVED_OFFERS_REQUEST_LIMIT = 24;
