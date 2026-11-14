import { Link } from '../router';
import { EmptyState } from '../ui';

export function NotFound() {
  return (
    <EmptyState title="Page not found">
      <p>
        That page doesn't exist. <Link href="/campaigns">Go to campaigns</Link>
      </p>
    </EmptyState>
  );
}
