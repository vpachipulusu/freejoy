import { Skeleton } from '@/components/ui/skeleton';
import { Spinner } from '@/components/ui/spinner';

/**
 * Route loading state: a visible spinner above skeleton blocks shaped like a page (title, lead, cards).
 * The skeleton keeps the layout from jumping when the page arrives; the spinner is what tells the person
 * the page is loading. The region is the one `role="status"` (the spinner inside it is decorative), so
 * assistive technology announces "Loading" once.
 */
export function RouteSkeleton() {
  return (
    <div className="container py-12">
      <div role="status" aria-busy="true" aria-label="Loading" className="space-y-8">
        <span className="sr-only">Loading</span>
        <div className="flex justify-center">
          <Spinner decorative size="lg" />
        </div>
        <div className="space-y-3">
          <Skeleton className="h-9 w-64 max-w-full" />
          <Skeleton className="h-5 w-96 max-w-full" />
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Skeleton className="h-32 rounded-lg" />
          <Skeleton className="h-32 rounded-lg" />
          <Skeleton className="h-32 rounded-lg" />
        </div>
        <Skeleton className="h-64 w-full rounded-lg" />
      </div>
    </div>
  );
}
