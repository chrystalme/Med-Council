# web

Next.js 16 frontend for the MedAI Council. App Router, Tailwind v4, Clerk auth.

## Dev

From the repo root:

```bash
pnpm install
pnpm dev            # Next.js on :3000
```

The dev server proxies `/api/*` to the FastAPI backend (default
`http://localhost:8000`; override via `NEXT_PUBLIC_API_BASE_URL`). Start the
API separately with `pnpm run api:dev`.

## Env

Copy `.env.example` to `.env.local` and fill in Clerk keys. The app boots in
Clerk Keyless mode without keys and auto-provisions a dev app on first load.

## Build

```bash
pnpm build          # production build
pnpm typecheck      # tsc --noEmit
pnpm lint           # eslint
```
