'use client';

type GoogleIdentity = {
  initialize(options: { client_id: string; nonce: string; callback(response: { credential: string }): void }): void;
  renderButton(element: HTMLElement, options: Record<string, string | number>): void;
};

declare global { interface Window { google?: { accounts: { id: GoogleIdentity } } } }

let pending: Promise<GoogleIdentity> | null = null;

/** Load Google's actual identity SDK once; a failed load remains retryable. */
export function loadGoogleIdentity(): Promise<GoogleIdentity> {
  if (window.google?.accounts?.id) return Promise.resolve(window.google.accounts.id);
  if (pending) return pending;
  const loading = new Promise<GoogleIdentity>((resolve, reject) => {
    const script = document.createElement('script');
    script.src = 'https://accounts.google.com/gsi/client';
    script.async = true;
    script.dataset.miloGoogleIdentity = 'true';
    const finish = (failure?: Error) => {
      window.clearTimeout(timeout);
      script.onload = null; script.onerror = null;
      if (failure || !window.google?.accounts?.id) {
        script.remove();
        reject(failure ?? new Error('Google sign-in did not initialize. Try again.'));
      } else resolve(window.google.accounts.id);
    };
    const timeout = window.setTimeout(() => finish(new Error('Google sign-in took too long to load. Try again.')), 15_000);
    script.onload = () => finish();
    script.onerror = () => finish(new Error('Google sign-in could not load. Check your connection and try again.'));
    document.head.appendChild(script);
  });
  pending = loading;
  // Clear only this attempt so a failed SDK request cannot poison later visits.
  void loading.catch(() => { if (pending === loading) pending = null; });
  return loading;
}
