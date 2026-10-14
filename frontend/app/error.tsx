"use client";

// Last line of defence: if rendering ever throws, show this instead of Next's blank error page.
export default function Error({ reset }: { error: Error; reset: () => void }) {
  return (
    <main>
      <h1 className="brand">RepoCompass</h1>
      <div className="notice bad" role="alert">
        <p>Something went wrong while showing this page.</p>
        <button type="button" className="link" onClick={reset}>
          Try again
        </button>
      </div>
    </main>
  );
}
