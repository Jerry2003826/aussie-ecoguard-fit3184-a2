# Aussie EcoGuard web

React / TypeScript / Vite interface for the `/api/v1` service. No cloud resources are created by this package.

```powershell
npm ci
Copy-Item .env.example .env.local
npm run dev
npm run build
npm test
```

Set `VITE_API_BASE_URL` (defaults to `/api/v1`), `VITE_COGNITO_USER_POOL_ID` and `VITE_COGNITO_CLIENT_ID`. The browser app client must not have a client secret. AWS Amplify handles sign-up, email confirmation, temporary-password challenges, reset and session refresh. Requests use the **access token**, not ID tokens. See [official Amplify sign-in documentation](https://docs.amplify.aws/nextjs/frontend/auth/sign-in/).

The development server proxies `/api` to `http://127.0.0.1:8000`. Set `VITE_DEV_MODE=true` only for the explicitly labelled local adapter. It is ignored outside localhost/127.0.0.1/::1. Choose Alice or Bob in the local test panel; requests carry `X-Ecoguard-Dev-User`. The server must independently require local mode. Local mode supplies no AI detections, email delivery or Lex responses and cannot establish cloud acceptance.

All responses use `{ data: ... }`, errors use `{ error: { code, message, retryable }, requestId }`. Media uses `url` as its stable management URL. Files go to the exact upload URL with server-provided headers; Cognito tokens are not forwarded to upload storage. Tasks poll with backoff and preserve last-known status on network errors. Query IDs are scoped to the signed-in user in session storage for recovery; no file bytes or credentials are stored there by application code.

UI routes: `/media`, `/media/:id`, `/search`, `/pets`, `/subscriptions`; the Lex chat panel is available throughout authenticated pages. Production hosting must route application paths to `index.html`; `/api` must point to the API separately. Cloud authentication, notifications, inference and Lex require deployed backends and are not validated by the local build/test checks.

The UI uses system font fallbacks if Google Fonts cannot be loaded. Build emits immutable static files in `dist/`. Do not commit `.env.local`, build artifacts or real user media.
