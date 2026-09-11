// Admin auth external store — avoids setState-in-effect and hydration mismatch.
// getServerSnapshot returns false so SSR/prerender show the login screen safely.

const listeners = new Set<() => void>();

export function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

export function getSnapshot(): boolean {
  return typeof window !== "undefined" && sessionStorage.getItem("admin_auth") === "1";
}

export function getServerSnapshot(): boolean {
  return false;
}

export function signIn(): void {
  sessionStorage.setItem("admin_auth", "1");
  listeners.forEach((l) => l());
}

export function signOut(): void {
  sessionStorage.removeItem("admin_auth");
  listeners.forEach((l) => l());
}
